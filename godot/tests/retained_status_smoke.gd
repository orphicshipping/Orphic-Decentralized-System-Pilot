extends SceneTree
## Exercise the actual projected labels, including both card status spellings.
const Catalog = preload("res://scripts/usecase_catalog_view.gd")
const Kit = preload("res://scripts/presentation_kit.gd")
var failed := false
var checks := 0

func require(value: bool, label: String) -> void:
	checks += 1
	if not value:
		push_error(label)
		failed = true

func _initialize() -> void:
	call_deferred("run")

func run() -> void:
	var view := Catalog.new()
	view.render_path = "user://absent-retained-regression.json"
	root.add_child(view)
	await process_frame
	require(view.current_status == "STALE", "Missing input stays stale")
	var path := "user://retained-status-regression.json"
	for flag: Variant in [false, null, "true", 1, true]:
		for status: String in ["VERIFIED", " verified ", "\tVERIFIED\n"]:
			for field: String in ["status", "verification_status"]:
				var payload := {"status": status, "cards": [{field: status}], "may_authorize": true}
				if flag != null:
					payload["fresh_verifier_occurrence"] = flag
				var file := FileAccess.open(path, FileAccess.WRITE)
				file.store_string(JSON.stringify(payload))
				file.close()
				view.load_render(path)
				var fresh: bool = typeof(flag) == TYPE_BOOL and flag == true
				require(view.current_status == ("VERIFIED" if fresh else "HISTORICAL"), "Top-level status must honor strict freshness")
				require(view._status.text == ("VERIFIED" if fresh else Kit.format_status_label("HISTORICAL")), "Visible top-level label must honor freshness")
				require(view._body.text.contains(field.replace("_", " ") + ": " + ("VERIFIED" if fresh else Kit.format_status_label("HISTORICAL"))), "Visible card status must honor freshness")
				require((Kit.verification_label(payload) == "VERIFIED") == fresh, "Shared kit must reject truthy non-booleans")
	var malformed := FileAccess.open(path, FileAccess.WRITE)
	malformed.store_string("[]")
	malformed.close()
	view.load_render(path)
	require(view.current_status == "REFUSED" and view.current_render.is_empty(), "Malformed render refuses and clears previous values")
	DirAccess.remove_absolute(ProjectSettings.globalize_path(path))
	view.queue_free()
	await process_frame
	if not failed:
		print("NCP_RETAINED_STATUS_SMOKE_PASS checks=", checks)
	quit(1 if failed else 0)
