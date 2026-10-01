# Terraform and Ansible deployment

This deployment is a separate stack from the GPU or Slurm cluster. Terraform creates a private query-serving tier; Ansible configures each instance's directly attached Block Volumes, LVM/XFS hot tier, and the existing Python search service.

## Architecture and ownership

```text
GPU or application subnet
          |
          | private HTTP request
          v
private load balancer (optional)
          |
          v
search-service node(s) -- direct, non-shareable Block Volume(s) -- LVM/XFS -- LanceDB hot table
          |
          +-- instance principal --> OCI Object Storage raw payload bucket
```

The service returns top-k identifiers, metadata, and source URIs. Original documents and media remain in Object Storage. A GPU node uses its own instance principal to retrieve a selected object; it does not mount the search database or receive the service node's Object Storage credentials.

Every service node owns a local hot tier. Do not configure a Block Volume as shareable and do not mount it from GPU nodes. Scale uses explicit database shards and replicas, each with its own local hot tier, not a shared filesystem.

## Object Storage update pipeline

When `enable_object_event_ingestion` is enabled, Terraform creates a regional
Events rule in the selected compartment. OCI rules apply to that compartment
and child compartments, so the rule receives Object Storage create, update, and
delete events from every enabled bucket in scope. OCI Events cannot write
directly to OCI Queue; the stack deploys a lightweight Function that copies the
event unchanged to a Queue. The first search node is the single active Queue
consumer and fans a normalized delta to a private ingestion worker on every
search-node replica.

```text
Object Storage event -> OCI Function -> OCI Queue -> controller on search node 0
                                                     -> worker on every search node
                                                     -> local hot LanceDB replica
```

The event rule intentionally has broad compartment scope. The **source-routing
registry** is the safety boundary: only matching bucket/prefix entries in the
ORM `ingestion_source_routes_json` input are applied, and longest matching
prefix wins. Terraform emits this setting into the generated Ansible inventory;
it is not an after-deployment manual edit. Other events are acknowledged
without a table change. Route changes and moving a prefix between tables are
controlled data migrations.

Use one shared, private OCIR repository for this solution. Create the repository
once per tenancy/region, then build and publish versioned router images to it.
Every ORM deployment references the selected immutable image tag (preferably a
digest); it does not create a per-stack repository or rebuild the image.

For the initial image publication:

```bash
cd function/object_event_router
docker build -t iad.ocir.io/<namespace>/<repository>:0.1.0 .
docker push iad.ocir.io/<namespace>/<repository>:0.1.0
```

Set that image reference in `ingestion_function_image`. For an existing bucket,
enable object events explicitly; Terraform can enable them only for a bucket it
creates:

```bash
oci os bucket update --name <bucket> --object-events-enabled true
```

The Queue is at-least-once and event ordering is not assumed. The controller
deletes a message only after every worker acknowledges it. Workers apply updates
by stable object ID and should be configured with an approved embedding provider
before production. The included deterministic provider is only a lifecycle and
replica-synchronization test harness.

The initial target table is created on first ingest if it does not exist, with
`id`, `source_uri`, `object_version`, `event_time`, and `vector` columns. An
existing table must expose compatible columns and use the configured vector
dimension. Do not point the route at an unrelated benchmark table whose schema
or identifier type differs.

## Prerequisites

- OCI CLI/API credentials capable of creating the requested resources, or Oracle Resource Manager.
- An existing VCN and private subnet reachable from GPU/application subnet(s). These may be outputs of `oci-hpc`.
- A current Ubuntu image OCID for the search compute shape.
- A narrowly scoped existing dynamic group that includes the service instances if the stack will create Object Storage policies. Do not create an unrestricted dynamic group merely for this stack.
- A Git URL and revision for this repository after it is published.
- The block device names from the created instances. Ansible deliberately requires explicit values; verify with `lsblk -o NAME,SERIAL,SIZE,TYPE` rather than relying on attachment order.

## Terraform

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
# Edit every example OCID and setting.
terraform init
terraform plan
terraform apply
terraform output -raw ansible_inventory > ../ansible/inventory/hosts.ini
```

For Resource Manager, upload the `infra/` directory as the stack. `schema.yaml` provides its required input form. It expects an existing VCN and private subnet rather than recreating the GPU-cluster network, and validates that the selected subnet belongs to the selected VCN.
The form first lists VCNs from the selected network compartment, then lists only
private subnets from the selected VCN. The VCN picker displays names but passes
the selected OCID to Terraform as `vcn_id`.
Select the separate GPU/application private subnet as well; its CIDR block is
read by Terraform and used as the search-service ingress source automatically.
The payload bucket is assumed to be in the same tenancy, so its Object Storage
namespace is discovered automatically and is not an input parameter.
Select Terraform **1.5.x** for the Resource Manager stack; the configuration is
pinned to that ORM-supported release line.

For an initial single-node feasibility deployment, use one 500 GB Balanced volume and `hot_tier_vpus_per_gb = 10`. A larger node-local hot tier can use several 500 GB volumes striped through LVM. This is a practical high-IOPS standardization choice; exact volume count, instance attachment capacity, and the service's query concurrency must be benchmarked together.

## Ansible

```bash
cd ../ansible
cp inventory/hosts.ini.example inventory/hosts.ini   # Terraform normally generates this file.
cp group_vars/lancedb_search.yml.example group_vars/lancedb_search.yml
# Edit the Git URL, table schema values, bucket, and explicit volume device paths.
ansible-playbook playbooks/search-service.yml
```

The role creates an LVM stripe set only on the explicit block devices passed in `lancedb_block_devices`. The initial run is destructive only to those declared empty Block Volumes because it initializes their LVM metadata and XFS filesystem. Do not point it at a boot volume or an already populated hot tier.

The generated inventory also sets `lancedb_host_firewall_source_cidrs` to the
private LB subnet CIDR. The Ansible role installs a systemd-managed firewall
helper that permits only this CIDR to reach the search API port before Ubuntu's
default catch-all firewall rule. This protects both LB health checks and
forwarded requests across node reboots. When maintaining inventory manually,
set this variable to the LB subnet CIDR explicitly. Prefer a dedicated LB
subnet so the CIDR is not shared with unrelated clients.

The infrastructure stack installs the event-to-worker delivery path. Source
reconciliation, compaction, index construction, embedding-provider selection,
and shard routing remain controlled data-plane operations.

## Required operations after deployment

1. Configure the bucket policy/dynamic group and verify `oci os ns get --auth instance_principal` from a service node.
2. Load the hot LanceDB table and build the selected vector or full-text index.
3. Verify `/healthz` from an allowed GPU/application node.
4. Run query, p95/p99 latency, Block Volume IOPS/throughput, Object Storage request-rate, and GPU-utilization tests.
5. Configure a snapshot/export, recovery, replica rebuild, and version-reconciliation process before accepting production traffic.
