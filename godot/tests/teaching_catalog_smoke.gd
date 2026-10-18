extends SceneTree
const Catalog = preload("res://scripts/usecase_catalog_view.gd")
var checks: int = 0
var failed: bool = false

func require(value: bool, label: String) -> void:
	if not value:
		push_error(label)
		failed = true
		return
	checks += 1

func _initialize() -> void:
	call_deferred("run")

func run() -> void:
	var view := Catalog.new()
	root.add_child(view)
	await process_frame
	require(view.entries.size() == 10, "All ten input-backed teaching families must load")
	for index: int in range(view.entries.size()):
		view.select_entry(index)
		require(not view.current_render.is_empty(), "Every selected teaching render must load")
		require(view.current_render.get("may_authorize") == false, "Teaching render must not authorize")
		require(view.current_render.get("fresh_verifier_occurrence") == false, "Export must not invent verification")
	var original_root: String = view.repository_root
	view.repository_root = "user://missing-teaching-catalog"
	view.load_catalog()
	require(view.current_status == "STALE", "Missing catalog must be STALE")
	view.repository_root = original_root
	view.load_catalog()
	view.select_entry(-1)
	require(view.current_status == "REFUSED", "Invalid selection must refuse")
	view.queue_free()
	await process_frame
	if not failed:
		print("CIW_TEACHING_CATALOG_SMOKE_PASS checks=", checks)
	quit(1 if failed else 0)
