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

The infrastructure stack stops after the service is healthy. Ingestion, source-object reconciliation, compaction, index construction, and shard/replica routing are data-plane operations and should be executed by a separate controlled pipeline once the table schema and change semantics are approved.

## Required operations after deployment

1. Configure the bucket policy/dynamic group and verify `oci os ns get --auth instance_principal` from a service node.
2. Load the hot LanceDB table and build the selected vector or full-text index.
3. Verify `/healthz` from an allowed GPU/application node.
4. Run query, p95/p99 latency, Block Volume IOPS/throughput, Object Storage request-rate, and GPU-utilization tests.
5. Configure a snapshot/export, recovery, replica rebuild, and version-reconciliation process before accepting production traffic.
