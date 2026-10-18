extends SceneTree
## Presentation kit smoke: missing JSON → STALE; with analog_render → cards non-empty.
## Godot may be absent from PATH; keep this script for local runs:
##   godot --headless --path godot --script res://tests/presentation_smoke.gd

const AnalogStateView = preload("res://scripts/analog_state_view.gd")
const Kit = preload("res://scripts/presentation_kit.gd")

var _failures: Array[String] = []


func _check(name: String, ok: bool) -> void:
	print("  %s  %s" % ["PASS" if ok else "FAIL", name])
	if not ok:
		_failures.append(name)


func _initialize() -> void:
	_run.call_deferred()


func _run() -> void:
	print("presentation kit smoke")
	_check("status_vocab_has_historical", Kit.STATUS_VOCAB.has("HISTORICAL"))
	_check(
		"historical_label_maps",
		Kit.format_status_label("HISTORICAL") == "retained_runtime_report_requires_fresh_verification"
	)
	_check("verified_requires_fresh_flag", Kit.verification_label({}) == "VERIFICATION  /  NOT CLAIMED")
	_check(
		"verified_when_fresh",
		Kit.verification_label({"fresh_verifier_occurrence": true}) == "VERIFIED"
	)

	# Exercise the retained geometry component actually present in this checkout.
	# The old full Analog tab and its file loader are not shipped here.
	var state := AnalogStateView.new()
	root.add_child(state)
	await process_frame
	await process_frame
	_check("geometry_starts_stale", state._stale)
	state.set_render({"trajectories": {"renewal": {"points": [[0, 0, 0], [1, 1, 1]]}}})
	_check("retained_points_projected", state._renewal_points.size() == 2)
	state.set_stale(false)
	_check("explicit_fresh_display_state", not state._stale)
	state.set_stale(true)
	_check("stale_state_restored", state._stale)
	state.clear_view()
	_check("clear_removes_retained_geometry", state._renewal_points.is_empty() and state._surface.mesh == null)
	state.queue_free()

	if _failures.is_empty():
		print("PASS: presentation smoke")
		quit(0)
	else:
		push_error("FAIL: " + ", ".join(_failures))
		quit(1)
