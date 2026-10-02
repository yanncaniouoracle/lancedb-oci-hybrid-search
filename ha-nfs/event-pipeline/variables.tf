variable "region" { type = string }
variable "compartment_ocid" { type = string }
variable "deployment_name" {
  type        = string
  description = "Lower-case identifier; use a different value from the direct-BV stack."
}
variable "functions_subnet_ocid" {
  type        = string
  description = "Private subnet with egress to OCI Queue, for the router Function."
}
variable "function_nsg_ids" {
  type        = list(string)
  description = "Optional existing NSGs attached to the Functions application."
  default     = []
}
variable "ingestion_function_image" {
  type        = string
  description = "Pre-built OCIR router image, preferably an immutable digest."
}
variable "queue_retention_seconds" {
  type    = number
  default = 345600
}
variable "queue_visibility_seconds" {
  type    = number
  default = 120
}
variable "function_memory_mbs" {
  type    = number
  default = 256
}
variable "create_function_queue_policy" {
  type        = bool
  default     = false
  description = "Create only when tenancy policy administration is authorized."
}
variable "common_freeform_tags" {
  type    = map(string)
  default = {}
}
