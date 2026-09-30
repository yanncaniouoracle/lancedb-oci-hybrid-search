output "search_service_private_ips" {
  description = "Private IP addresses of directly attached hot-tier service nodes."
  value       = { for key, instance in oci_core_instance.search : key => instance.private_ip }
}

output "search_service_endpoint" {
  description = "Private load-balancer endpoint when enabled; otherwise call a returned node address or add shard-aware routing."
  value       = var.enable_private_load_balancer ? "http://${oci_load_balancer_load_balancer.search[0].ip_address_details[0].ip_address}:${var.service_port}" : null
}

output "hot_volume_ids" {
  description = "Block Volume OCIDs grouped by search node."
  value = {
    for node in keys(local.search_nodes) : node => [
      for key, attachment in oci_core_volume_attachment.hot : attachment.volume_id
      if tostring(local.volume_attachments[key].node_index) == node
    ]
  }
}

output "ansible_inventory" {
  description = "Save this output as ansible/inventory/hosts.ini before running the playbook."
  value = join("\n", concat(
    ["[lancedb_search]"],
    [for key, instance in oci_core_instance.search : "${var.deployment_name}-${key} ansible_host=${instance.private_ip}"],
    ["", "[lancedb_search:vars]", "ansible_user=ubuntu"]
  ))
}

output "object_storage_location" {
  description = "Raw-payload bucket location referenced by hot-table source_uri values."
  value       = "s3://${var.object_storage_bucket_name}/${var.object_storage_prefix}"
}
