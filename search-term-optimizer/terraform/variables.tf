variable "project_id" {
  type = string
}

variable "location" {
  type    = string
  default = "europe-west1"
}

variable "image" {
  description = "Image Docker complète, ex. europe-west1-docker.pkg.dev/PROJET/sto/sto:v1"
  type        = string
}

variable "google_oauth_client_id" {
  description = "ID du client OAuth (type Application Web)"
  type        = string
}

variable "google_oauth_client_secret" {
  type      = string
  sensitive = true
}

variable "mcc_id" {
  description = "Identifiant du MCC Google Ads, sans tirets"
  type        = string
}

variable "google_ads_api_token" {
  description = "Developer token Google Ads"
  type        = string
  sensitive   = true
}

variable "gemini_api_key" {
  description = "Clé API Gemini (AI Studio)"
  type        = string
  sensitive   = true
}

variable "gemini_model" {
  type    = string
  default = "gemini-3.1-flash-lite"
}
