output "ingestion_queue_id" {
  value = oci_queue_queue.object_events.id
}

output "ingestion_queue_endpoint" {
  value = oci_queue_queue.object_events.messages_endpoint
}

output "object_event_rule_id" {
  value = oci_events_rule.object_changes.id
}

output "router_function_id" {
  value = oci_functions_function.router.id
}
