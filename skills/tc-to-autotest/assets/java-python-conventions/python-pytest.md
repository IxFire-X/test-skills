# Python / pytest V5 convention

Use only after authorized discovery confirms Python and pytest; do not add dependencies or replace project-native fixtures. Direct canonical/provider-backed execution runs global provider/adapter preflight; an exact accepted generated-source chain executes through the project-native pytest process without fabricated provider values.

Target the selected module's active test root. The controller materializes reviewed files
and invokes only `pytest:selected-symbols-v1` with the frozen interpreter/profile;
generation never starts pytest itself.

Every generated symbol has identity `(file_id, symbol_id)` and a `python_module_function` or `python_class_method` locator. Emit atomic operation/assertion relations for each covered canonical target; required pairs are AND-combined. A correction is a full second V5 artifact bound to the first automation and `AUTO_FIX_APPLIED` review digests; never emit a partial patch or regenerate after runtime `FAIL`.

At the `http-binding-v1` boundary perform one transport attempt. Use no implicit redirects, no retries, no cookies, no auth, no default headers, and no decompression. Preserve ordered explicit headers and use only runtime secret handles. Do not claim execution from static generation.
