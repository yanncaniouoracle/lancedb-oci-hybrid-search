# HA-NFS alternative: shared LanceDB table on Block Volume

This directory is an **overlay for the OCI HA-NFS reference deployment**.  It
is the alternative to the `infra/` Search Service on Compute stack: GPU or
application nodes mount a shared NFSv4.1 export and open the LanceDB table
directly.  There is no FastAPI search service and no OCI Load Balancer.

```text
GPU/application nodes -- NFSv4.1 --> HA VIP --> active NFS server
                                            --> shared 2 x 500 GB Balanced BV
                                                 LVM stripe + XFS + LanceDB

Object Storage events --> Function --> Queue --> active NFS ingestion worker
                                                + reconciliation timer
```

The upstream project owns the HA storage plane: two NFS servers, a quorum
node, a multi-attach SBD fencing volume, Pacemaker/corosync, the movable VIP,
shared LVM activation, XFS and NFS.  This overlay adds the LanceDB data plane
and mounts the resulting export on externally managed GPU nodes.

## Deliberate concurrency model

The exported table is shared; it is not a collection of replicas.  Exactly one
Pacemaker resource group owns the filesystem, NFS server, ingestion worker,
queue controller and reconciliation timer.  The controller sends every event
to `127.0.0.1`, so there is one writer to the shared table.  Pacemaker moves
all of those resources together during a failover.  GPU nodes are read-only
LanceDB clients unless the workload has an explicit single-writer procedure.

## Create the second stack (do not apply yet)

1. Obtain the OCI HA-NFS project at a reviewed release:

   ```bash
   git clone https://github.com/oracle-quickstart/oci-nfs.git
   cd oci-nfs
   ```

2. Copy [`terraform.tfvars.example`](terraform.tfvars.example) to
   `terraform.tfvars` in that checkout and set OCIDs, AD, image/shape and
   subnet values.  It configures **HA**, two shared 500 GB Balanced volumes,
   LVM striping, and no project-created client nodes.  Existing GPU nodes are
   added later to this overlay inventory.

3. Apply the upstream stack.  Its generated inventory is the input for the
   overlay.  Add the GPU nodes to the `[gpu_nodes]` group and values from
   [`ansible/group_vars/all.yml.example`](ansible/group_vars/all.yml.example).

4. Run the upstream `playbooks/site.yml` first.  Then run this overlay:

   ```bash
   ansible-galaxy collection install -r ansible/requirements.yml
   ansible-playbook -i inventory/hosts.ini \
     ansible/playbooks/lancedb-ha-nfs-overlay.yml
   ```

The overlay intentionally refuses to proceed until the upstream `nfsgroup`
Pacemaker resource group is present.  It does not create, reformat or attach a
Block Volume; therefore it cannot accidentally overwrite the shared NFS data
plane.

## Required OCI event resources

[`event-pipeline/`](event-pipeline) is a small, separate Terraform component
for the NFS alternative.  It creates a distinct Function -> Queue -> Event
Rule pipeline using the same pre-built router image.  The queue-consumer dynamic
group must include **both NFS servers**, because either may become active.  Its
outputs become `lancedb_ingestion_queue_id` and
`lancedb_ingestion_queue_endpoint` in the overlay group variables.

Do **not** point the direct-BV search-service controller and the HA-NFS
controller at the same Queue: OCI Queue delivers each message to one consumer,
not both.  During a comparison, use separate queues and source routes, or
disable one event rule/controller pair.  This separation avoids importing or
replacing the tested upstream Pacemaker resources.  A future consolidation can
compose the upstream NFS Terraform as a pinned module once its lifecycle and
provider versions have been validated in ORM.

## Resulting paths

* NFS server table: `/mnt/nfsshare/exports/lancedb-hot`
* GPU-node mount: `/mnt/lancedb-hot`
* GPU-node table: `/mnt/lancedb-hot/lancedb-hot`

The mount uses `hard,_netdev,nfsvers=4.1,proto=tcp,noatime`.  `hard` is
important: a soft NFS mount can return I/O errors to LanceDB during an HA
failover and risks application-level corruption.
