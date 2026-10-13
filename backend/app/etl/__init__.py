"""File-only ETL for the historical hardware dataset."""

SOURCE = "docyx/pc-part-dataset"
DATASET_UPDATED_AT = "2025-07-23"
EXPECTED_SHA256 = "5c3073dbe3ea2241c8b907b0a119bec6992fa4d3335c958569027c2f066b49ab"
PIPELINE_VERSION = "m01.1"

CATEGORIES = {
    "cpu": "CPU",
    "motherboard": "MOTHERBOARD",
    "video-card": "GPU",
    "memory": "MEMORY",
    "internal-hard-drive": "STORAGE",
    "power-supply": "PSU",
    "case": "CASE",
    "cpu-cooler": "COOLER",
}

SOURCE_FIELDS = {
    "cpu": "name price core_count core_clock boost_clock microarchitecture tdp graphics",
    "motherboard": "name price socket form_factor max_memory memory_slots color",
    "video-card": "name price chipset memory core_clock boost_clock color length",
    "memory": "name price speed modules price_per_gb color first_word_latency cas_latency",
    "internal-hard-drive": "name price capacity price_per_gb type cache form_factor interface",
    "power-supply": "name price type efficiency wattage modular color",
    "case": "name price type color psu side_panel external_volume internal_35_bays",
    "cpu-cooler": "name price rpm noise_level color size",
}
