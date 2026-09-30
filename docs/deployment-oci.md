# OCI deployment guide

For a repeatable multi-node deployment, use the Terraform and Ansible stack in
[Terraform and Ansible deployment](terraform-ansible-deployment.md). This guide
remains useful for the single-instance feasibility setup.

## 1. Provision the search tier

Attach and mount a dedicated Block Volume directly on the search instance. Use a stable mount such as `/mnt/lancedb-hot`, owned by the service account. The database directory must survive process and Python-session termination.

Keep the database local to this tier. GPU/application nodes call the service; they do not mount the Block Volume or open the LanceDB directory.

## 2. Install the service

```bash
sudo mkdir -p /opt/oci-lancedb-hybrid /etc/lancedb-search
sudo chown ubuntu:ubuntu /opt/oci-lancedb-hybrid
python3 -m venv /opt/oci-lancedb-hybrid/.venv
source /opt/oci-lancedb-hybrid/.venv/bin/activate
pip install -e '.[service,mnist]'
sudo cp config/lancedb.env.example /etc/lancedb-search/lancedb.env
sudo cp systemd/lancedb-search.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lancedb-search
```

For the MNIST feasibility setup, run `oci-lancedb-ingest-mnist` on the search
tier or a dedicated ingestion worker. It needs an explicitly authorized
Object Storage writer credential; do not give that credential to GPU nodes.

Set `LANCEDB_BIND_HOST` to the search instance's private VCN address. Confirm locally with `curl http://PRIVATE_IP:8080/healthz`.

## 3. Network rules

Allow TCP/8080 only from the GPU/application node CIDR or security group:

- OCI NSG/security list: stateful ingress from the client source to TCP/8080.
- Ubuntu firewall: allow the same source and port before the image's default reject rule. Persist the rule with `netfilter-persistent save`.

For example:

```bash
sudo iptables -I INPUT 5 -s GPU_PRIVATE_IP/32 -p tcp --dport 8080 -j ACCEPT
sudo netfilter-persistent save
```

## 4. Object Storage authorization

Prefer an OCI instance principal on GPU/application nodes. Create a dynamic group for those instances and grant it read access to the relevant Object Storage objects. The exact IAM policy scope must match the bucket compartment. Verify by running `oci-lancedb-validate`; no local access key should be required.

The search service itself does not need Object Storage access to answer a query when all hot data is on Block Volume. The ingestion worker needs write permission, and client nodes need read permission for selected payloads.

## 5. Test the data path

Run the validation client on a GPU node. It performs one archive read solely to obtain a known MNIST query vector, sends the vector to the search service, then fetches exactly one returned PNG URI. Record the search and selected-payload timings separately.

## 6. Move to a representative benchmark

Replace MNIST with SIFT1M first, then a representative application corpus. Measure sustained/peak queries per second, p50/p95/p99 search latency, Object Storage GET/range-GET counts, payload size, Block Volume IOPS/throughput, CPU utilization, and GPU idle time. Compare with the direct Object Storage LanceDB baseline under the same load and recall target.
