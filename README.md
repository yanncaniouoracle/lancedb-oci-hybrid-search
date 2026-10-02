# OCI LanceDB Hybrid Search

Reusable reference implementation for a LanceDB hot/cold architecture on OCI:

```text
GPU/application node -- private HTTP query --> search tier + direct Block Volume
       |                                              |
       +-- instance-principal Object Storage GET <----+  top-k s3:// URI
```

The search service stores and reads vectors, indexes, and searchable metadata from a directly attached Block Volume. Original payloads remain in OCI Object Storage. Search callers receive only result metadata and Object Storage URIs, then fetch payloads only when required.

## What this repository includes

- A FastAPI search service for a local LanceDB database.
- Instance-principal Object Storage reads for GPU/application nodes.
- An MNIST feasibility dataset flow: 60,000 local vectors, with one PNG object per row in Object Storage.
- A GPU-side end-to-end validation command.
- A systemd unit and OCI deployment checklist.
- A standalone Terraform plus Ansible deployment stack for private search
  compute, direct Block Volume hot tiers, Object Storage access, and optional
  private load balancing.
- An event-driven update path: OCI Object Storage events flow through a small
  OCI Function into OCI Queue; the first search node fans each delta to one
  private ingestion worker on every search-node replica.

MNIST validates the architecture only. Use SIFT1M or a representative corpus for throughput and latency testing.

## Terraform and Ansible stack

The [`infra/`](infra) directory is a second OCI stack, designed to consume the
VCN/subnet/CIDR outputs of a GPU or `oci-hpc` deployment without coupling the
two lifecycles. Terraform creates private search compute, directly attached
non-shareable Block Volumes, Object Storage access, NSGs, and an optional
private load balancer. The [`ansible/`](ansible) role configures node-local
LVM/XFS and the service. See the [deployment guide](docs/terraform-ansible-deployment.md).

## HA-NFS alternative

[`ha-nfs/`](ha-nfs) provides the alternative shared-filesystem design. It uses
the OCI HA-NFS reference stack for two active/passive NFS servers, quorum,
fencing, a movable VIP, two shared 500 GB Balanced Block Volumes and a striped
XFS filesystem. Its LanceDB overlay mounts the NFS export on existing GPU
nodes and places the ingestion worker, Queue controller, and reconciliation
timer in the same Pacemaker resource group as the filesystem. It deliberately
has no search service or load balancer. See the [HA-NFS guide](ha-nfs/README.md).

## Quick start

Follow [the OCI deployment guide](docs/deployment-oci.md). The standard validation command on a GPU node is:

```bash
source /etc/lancedb-search/lancedb.env
/opt/oci-lancedb-hybrid/.venv/bin/oci-lancedb-validate
```

## Security model

Use an OCI instance principal on client nodes. The node's dynamic group needs only read access to objects in the target bucket. Do not place Object Storage customer secret keys in this repository or copy the search tier's write credential to GPU nodes.

## Scope

This is a reference service, not a complete multi-tenant control plane. Production deployments should add authentication/authorization, tenant-safe filter construction, request limits, tracing/metrics, load balancing or shard-aware routing, and lifecycle management. The included ingestion worker uses a deterministic test embedding; replace it with the workload's approved embedding provider before production use.
