variable "region" {
  description = "OCI region in which to create the search tier."
  type        = string
}

variable "compartment_ocid" {
  description = "Compartment that owns the search instances, volumes, and bucket."
  type        = string
}

variable "availability_domain" {
  description = "Availability Domain for search instances and Block Volumes."
  type        = string
}

variable "deployment_name" {
  description = "Lower-case deployment identifier used in resource names and tags."
  type        = string
  default     = "lancedb-search"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,30}$", var.deployment_name))
    error_message = "deployment_name must start with a lower-case letter and contain only lower-case letters, digits, and hyphens."
  }
}

variable "subnet_ocid" {
  description = "Existing private subnet for search-service instances and, if enabled, the private load balancer."
  type        = string
}

variable "vcn_id" {
  description = "Existing VCN selected in Resource Manager. The picker displays its name and passes its OCID as vcn_id."
  type        = string
}

variable "network_compartment_ocid" {
  description = "Compartment containing vcn_id and subnet_ocid. Defaults to compartment_ocid when network resources share the deployment compartment."
  type        = string
  default     = null
  nullable    = true
}

variable "ssh_public_key" {
  description = "SSH public key for emergency administration. Prefer Bastion for normal access."
  type        = string
}

variable "search_image_ocid" {
  description = "Ubuntu or Oracle Linux image OCID for search-service instances."
  type        = string
}

variable "search_shape" {
  description = "Compute shape for search-service instances."
  type        = string
  default     = "VM.Standard.E5.Flex"
}

variable "search_ocpus" {
  description = "OCPUs per search-service instance when using a flexible shape."
  type        = number
  default     = 8
}

variable "search_memory_gb" {
  description = "Memory in GB per search-service instance when using a flexible shape."
  type        = number
  default     = 64
}

variable "search_service_count" {
  description = "Number of search-service nodes. Each node owns its own directly attached hot tier."
  type        = number
  default     = 1

  validation {
    condition     = var.search_service_count >= 1
    error_message = "search_service_count must be at least one."
  }
}

variable "boot_volume_size_gb" {
  description = "Boot-volume size in GB for each search node."
  type        = number
  default     = 100
}

variable "service_port" {
  description = "Private TCP port exposed by the search service."
  type        = number
  default     = 8080
}

variable "gpu_subnet_id" {
  description = "Private subnet containing GPU or application nodes allowed to call the search endpoint."
  type        = string
}

variable "enable_private_load_balancer" {
  description = "Create a private OCI Load Balancer in front of all search nodes."
  type        = bool
  default     = false
}

variable "load_balancer_min_bandwidth_mbps" {
  description = "Minimum private load-balancer bandwidth in Mbps."
  type        = number
  default     = 10
}

variable "load_balancer_max_bandwidth_mbps" {
  description = "Maximum private load-balancer bandwidth in Mbps."
  type        = number
  default     = 100
}

variable "hot_tier_volume_count" {
  description = "Directly attached Block Volumes per search node. LVM stripes these volumes on each node."
  type        = number
  default     = 1
}

variable "hot_tier_volume_size_gb" {
  description = "Size in GB of each hot-tier Block Volume. 500 GB is a practical Balanced high-IOPS brick size."
  type        = number
  default     = 500
}

variable "hot_tier_vpus_per_gb" {
  description = "Block Volume performance setting. Use 10 for Balanced performance."
  type        = number
  default     = 10

  validation {
    condition     = contains([0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120], var.hot_tier_vpus_per_gb)
    error_message = "hot_tier_vpus_per_gb must be a supported OCI VPU value."
  }
}

variable "object_storage_bucket_name" {
  description = "Existing or new bucket containing raw payloads and optional hot-tier exports."
  type        = string
}

variable "create_object_storage_bucket" {
  description = "Create object_storage_bucket_name. Set false when it already exists."
  type        = bool
  default     = false
}

variable "object_storage_prefix" {
  description = "Prefix for raw objects referenced by the hot table."
  type        = string
  default     = ""
}

variable "create_instance_principal_policy" {
  description = "Create read-only Object Storage policies for an existing dynamic group."
  type        = bool
  default     = false
}

variable "dynamic_group_name" {
  description = "Existing dynamic-group name containing the search instances. The stack deliberately does not create a broad dynamic-group rule."
  type        = string
  default     = null
  nullable    = true
}

variable "enable_object_event_ingestion" {
  description = "Create the Object Storage event rule, OCI Function bridge, Queue, and search-node ingestion services."
  type        = bool
  default     = false
}

variable "ingestion_worker_port" {
  description = "Private port used by the controller to deliver updates to each search-node worker."
  type        = number
  default     = 8090
}

variable "ingestion_queue_retention_seconds" {
  description = "Retention for Object Storage event messages before they expire."
  type        = number
  default     = 345600
}

variable "ingestion_queue_visibility_seconds" {
  description = "Queue visibility timeout; must cover fan-out and retry handling."
  type        = number
  default     = 120
}

variable "ingestion_function_image" {
  description = "Pre-built OCIR image for function/object_event_router. Required when object-event ingestion is enabled."
  type        = string
  default     = null
  nullable    = true
}

variable "ingestion_function_memory_mbs" {
  description = "Memory allocated to the lightweight event-to-Queue Function."
  type        = number
  default     = 256
}

variable "common_freeform_tags" {
  description = "Free-form tags applied to created resources."
  type        = map(string)
  default     = {}
}
