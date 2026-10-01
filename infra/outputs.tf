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
    [
      "",
      "[lancedb_search:vars]",
      "ansible_user=ubuntu",
      "lancedb_host_firewall_source_cidrs=[\"${data.oci_core_subnet.search.cidr_block}\"]",
      "lancedb_ingestion_enabled=${var.enable_object_event_ingestion}",
      "lancedb_ingestion_worker_port=${var.ingestion_worker_port}",
      "lancedb_ingestion_worker_source_cidrs=[\"${data.oci_core_subnet.search.cidr_block}\"]",
      "lancedb_source_routes=${jsonencode(jsondecode(var.ingestion_source_routes_json))}",
      "lancedb_ingestion_queue_id=${var.enable_object_event_ingestion ? oci_queue_queue.object_events[0].id : \"\"}",
      "lancedb_ingestion_queue_endpoint=${var.enable_object_event_ingestion ? oci_queue_queue.object_events[0].messages_endpoint : \"\"}"
    ]
  ))
}

output "ingestion_pipeline" {
  description = "Object event rule and Queue endpoints when the ingestion pipeline is enabled. Existing source buckets must have object events enabled."
  value = var.enable_object_event_ingestion ? {
    event_rule_id  = oci_events_rule.object_changes[0].id
    queue_id       = oci_queue_queue.object_events[0].id
    queue_endpoint = oci_queue_queue.object_events[0].messages_endpoint
    function_id    = oci_functions_function.object_event_router[0].id
  } : null
}

output "ansible_host_firewall_source_cidrs" {
  description = "CIDR list that Ansible permits through the Ubuntu host firewall for the search API. It is the LB subnet CIDR."
  value       = [data.oci_core_subnet.search.cidr_block]
}

output "object_storage_location" {
  description = "Raw-payload bucket location referenced by hot-table source_uri values."
  value       = "s3://${var.object_storage_bucket_name}/${var.object_storage_prefix}"
}
