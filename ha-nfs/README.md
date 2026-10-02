# HA-NFS alternative: shared LanceDB table on Block Volume

The deployable, single ORM stack is now the
[`yanncaniouoracle/lancedb-oci-nfs`](https://github.com/yanncaniouoracle/lancedb-oci-nfs)
fork. It extends the OCI HA-NFS reference implementation with the LanceDB
event pipeline and Pacemaker-owned data plane. Use that repository as the
Resource Manager configuration source.

This directory remains as the design reference and standalone overlay source.
The architecture is the alternative to the `infra/` Search Service on Compute
stack: GPU or application nodes mount a shared NFSv4.1 export and open the
LanceDB table directly. There is no FastAPI search service and no OCI Load
Balancer.

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

## Create the second stack

1. Create an ORM stack from the integrated repository:

   ```bash
   git clone https://github.com/yanncaniouoracle/lancedb-oci-nfs.git
   cd lancedb-oci-nfs
   ```

2. Set the NFS HA fields and the LanceDB data-plane fields in the ORM UI. The
   storage configuration is **HA**, two shared 500 GB Balanced volumes, and
   LVM striping. Use a dedicated Function/Queue route for this alternative.

3. Apply the single integrated stack. Its built-in provisioning configures HA
   NFS, the Object Storage event path, and the Pacemaker-owned LanceDB writer.

4. Existing GPU nodes are external to the stack and require their own SSH
   access. Run the included `playbooks/lancedb-gpu-mount.yml` playbook from the
   fork’s bastion or an administration host after adding them to `[gpu_nodes]`.

   ```bash
   ansible-playbook -i gpu-inventory playbooks/lancedb-gpu-mount.yml
   ```

The GPU mount role refuses a local filesystem fallback. It uses a hard NFSv4.1
mount, which is important during a Pacemaker failover.

## Required OCI event resources

The integrated fork creates a distinct Function -> Queue -> Event Rule pipeline
using the pre-built router image. The queue-consumer dynamic group must include
**both NFS servers**, because either may become active.

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
