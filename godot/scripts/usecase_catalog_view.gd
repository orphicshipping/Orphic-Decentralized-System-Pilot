extends VBoxContainer
## Read-only projector over the same catalog/render files as the Bevy client.
const Kit = preload("res://scripts/presentation_kit.gd")
const FAMILIES := ["FSRT", "CSG", "CSE", "PROVED_HEAT", "RESIDUAL_CUSUM", "GTE_CIRCLE", "PLSR", "ENERGY_ACCURACY", "VFE", "MEASUREMENT_CHAIN"]
var repository_root: String = ProjectSettings.globalize_path("res://").path_join("..").simplify_path()
var entries: Array = []
var current_status: String = "STALE"
var current_render: Dictionary = {}
var render_path: String = ""
var _choice: OptionButton
var _status: Label
var _body: Label

func _read_record(path: String, limit: int) -> Variant:
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null or file.get_length() > limit:
		return null
	return JSON.parse_string(file.get_as_text())


func _ready() -> void:
	add_theme_constant_override("separation", 12)
	add_child(Kit.make_label("Retained teaching samples", 22))
	add_child(Kit.make_label("Declared inputs and documented outcomes. No equipment authority.", 14, Kit.MUTED))
	var row := HBoxContainer.new()
	add_child(row)
	_choice = OptionButton.new()
	_choice.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_choice.item_selected.connect(select_entry)
	row.add_child(_choice)
	var refresh := Button.new()
	refresh.text = "Refresh samples"
	refresh.pressed.connect(load_catalog)
	row.add_child(refresh)
	row.visible = render_path.is_empty()
	_status = Kit.make_label("STALE", 16, Kit.AMBER)
	add_child(_status)
	var scroll := ScrollContainer.new()
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	add_child(scroll)
	_body = Kit.make_label("", 14)
	_body.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(_body)
	add_child(Kit.make_label(Kit.CAPTION, 12, Kit.MUTED))
	if render_path.is_empty():
		load_catalog()
	else:
		load_render(render_path)


func _set_status(value: String, text: String) -> void:
	current_status = value
	_status.text = Kit.format_status_label(value)
	_status.modulate = Kit.status_color(value)
	_body.text = text


func load_catalog() -> void:
	entries.clear()
	_choice.clear()
	current_render.clear()
	var path := repository_root.path_join("examples/usecase-index.json")
	if not FileAccess.file_exists(path):
		_set_status("STALE", "The teaching catalog is unavailable.")
		return
	var parsed: Variant = _read_record(path, 4 * 1024 * 1024)
	if not parsed is Dictionary or parsed.get("schema") != "ncp.teaching-catalog.v1" or not parsed.get("entries") is Array:
		_set_status("REFUSED", "The teaching catalog has an unsupported format.")
		return
	var seen: Dictionary = {}
	var slug_pattern := RegEx.new()
	slug_pattern.compile("^[a-z0-9]+(-[a-z0-9]+)*$")
	for entry: Variant in parsed["entries"]:
		if not entry is Dictionary:
			_set_status("REFUSED", "Invalid teaching entry.")
			return
		var slug: String = str(entry.get("slug", ""))
		var expected := "examples/usecase-" + slug + "/results/" + slug + "_render.json"
		if slug_pattern.search(slug) == null or seen.has(slug) or not entry.get("family") in FAMILIES or entry.get("render_path") != expected:
			entries.clear()
			_set_status("REFUSED", "Invalid or uncontained teaching entry.")
			return
		seen[slug] = true
		entries.append(entry)
	if entries.is_empty():
		_set_status("STALE", "The teaching catalog contains no samples.")
		return
	for entry: Dictionary in entries:
		_choice.add_item(str(entry["title"]) + " · " + str(entry["family"]))
	_choice.select(0)
	select_entry(0)


func select_entry(index: int) -> void:
	current_render.clear()
	if index < 0 or index >= entries.size():
		_set_status("REFUSED", "Unknown teaching sample.")
		return
	var path: String = repository_root.path_join(entries[index]["render_path"])
	load_render(path)


func load_render(path: String) -> void:
	current_render.clear()
	if not FileAccess.file_exists(path):
		_set_status("STALE", "The selected teaching render is unavailable. Export the sample and refresh.")
		return
	var parsed: Variant = _read_record(path, 16 * 1024 * 1024)
	if not parsed is Dictionary:
		_set_status("REFUSED", "Invalid teaching render.")
		return
	current_render = parsed
	var fresh: bool = Kit.has_fresh_verifier(parsed)
	var state: String = Kit.retained_status(str(parsed.get("status", parsed.get("upstream_status", "UNAVAILABLE"))), parsed)
	var lines: Array[String] = [str(parsed.get("caption", "")), "VERIFIED" if fresh else "NOT VERIFIED"]
	var cards: Variant = parsed.get("cards", [])
	if cards is Dictionary:
		cards = cards.values()
	if cards is Array:
		for card: Variant in cards:
			if card is Dictionary:
				lines.append(str(card.get("title", card.get("label", "Retained value"))))
				for key: Variant in card:
					if key in ["title", "label", "may_authorize"]:
						continue
					var value: String = str(card[key])
					if key in ["status", "verification_status"]:
						value = Kit.format_status_label(Kit.retained_status(value, parsed))
					lines.append("  " + str(key).replace("_", " ") + ": " + value)
	_set_status(state, "\n\n".join(lines))
