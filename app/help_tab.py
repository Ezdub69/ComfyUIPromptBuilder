"""Static reference tab: what each tab does, plus a handful of workflow
tips. Pure static HTML in a QTextBrowser - no DB access, nothing dynamic,
just documentation that lives in the app instead of a separate file so it
can't go missing or get out of sync with wherever the app itself is.
"""

from PySide6.QtWidgets import QTextBrowser, QVBoxLayout, QWidget

_HELP_HTML = """
<style>
body { font-family: "Segoe UI", sans-serif; font-size: 13px; color: #1f2937; }
h2 { color: #1e3a8a; margin-top: 18px; margin-bottom: 4px; }
h2:first-child { margin-top: 0; }
h3 { color: #374151; margin-bottom: 2px; }
.tip { background: #ecfdf5; border-left: 3px solid #16a34a; padding: 6px 10px; margin: 6px 0; }
ul { margin-top: 4px; }
li { margin-bottom: 3px; }
</style>

<h2>LM Studio</h2>
<p><b>LM Studio</b> (a free download from lmstudio.ai) is the app that actually runs the AI
models - this app doesn't generate anything itself, it just sends requests to LM Studio's
local server and shows you the result. LM Studio needs to be running, with its local server
turned on, before anything on this tab or the two Krea 2 tabs will work.</p>
<ul>
<li>In LM Studio, open the Developer tab (the <code>&gt;_</code> icon on the left sidebar), turn the
server Status switch to on, and make sure <b>Enable CORS</b> is turned on in its settings - without
CORS this app's requests get silently blocked.</li>
<li>Download at least one model inside LM Studio itself first (its own Discover/search tab) - this
app can only detect and load what's already downloaded there, it can't fetch new models for you.</li>
<li>For the Krea 2 Assistant's Vision mode you need a model with actual vision/image support - a
text-only model will simply never show up in the Vision model list here, by design.</li>
</ul>
<p>This tab is the single place that controls which model each Krea 2 tab actually uses:</p>
<ul>
<li><b>Detect Models</b> - asks LM Studio what it has, and shows which of those are currently
loaded into memory vs just available.</li>
<li><b>Model assignments</b> - pick one model for the Prompt Builder, one for Assistant Vision, one
for Assistant Rewrite. These are remembered between sessions.</li>
<li><b>Load Assigned Models</b> - explicitly loads whichever models you just assigned, so they're
warm and ready instead of loading on your first real message (which just means waiting a bit
longer for that first response instead).</li>
<li><b>Unload All Models</b> - frees the memory LM Studio is using and resets the three
assignments back to blank, the same state as a first-ever launch. Nothing is deleted - loading
them again is one click away.</li>
</ul>
<div class="tip"><b>Tip:</b> closing this app does <i>not</i> unload anything from LM Studio -
whatever was loaded stays loaded. On a normal restart (without using Unload All Models), this
app checks automatically in the background whether your assigned models are still loaded and
just tells you - no click needed unless something's actually changed.</div>

<h3>Common errors and what they mean</h3>
<ul>
<li><b>"Connection failed" / can't reach the server</b> - LM Studio isn't running, its local
server is switched off, or the address on this tab is wrong. The default <code>http://localhost:1234</code>
is correct when LM Studio runs on the same PC as this app; if it's running on a different
computer on your network, use that computer's IP address instead, e.g.
<code>http://192.168.1.50:1234</code>.</li>
<li><b>"No models found" after Detect Models</b> - LM Studio is reachable, but has no models
downloaded yet. Download one from inside LM Studio first.</li>
<li><b>A model fails to load with an out-of-memory / resource error</b> - the model needs more
VRAM/RAM than your system has free. Try a smaller or more compressed (higher quantization
number, e.g. Q4 instead of Q8) version of the model, or unload other models first.</li>
<li><b>A response just stops partway through</b> - either the Stop button was clicked (the
reply gets an appended "[Stopped]"), or the model hit its Max tokens limit before finishing
(raise Max tokens on the Krea 2 Assistant tab for longer replies).</li>
</ul>

<h2>Krea 2 Prompt Builder</h2>
<p>Builds a Krea 2 prompt from structured fields instead of a chat - each field holds one
piece of the scene (medium, character, wardrobe, pose, scene, shot size, camera angle, mood),
so correcting one detail means editing that field and regenerating, not re-explaining the
whole image to a model that has to guess what changed.</p>
<ul>
<li><b>Medium / Shot size / Camera angle / Mood</b> - click-pickers, editable from Library
Settings.</li>
<li><b>Character / Wardrobe / Pose / Scene</b> - free text. Each has its own <b>Vary</b>
button, which asks the model for a fresh alternative for just that field, using the other
fields as context - useful for "just give me a different pose" without touching anything
else.</li>
<li><b>Generate</b> - sends every filled-in field as one single request and returns one
finished Krea 2 paragraph.</li>
<li><b>Explicit / uncensored</b> checkbox controls whether the generation instructions ask
for explicit content to be described directly rather than softened.</li>
<li><b>Save / Load</b> at the bottom stores the whole field set plus the last generated
output - shows up under "Krea 2 Prompts" on the Saved Prompts tab.</li>
</ul>
<div class="tip"><b>Tip:</b> "Detect Model" only lists what LM Studio actually has loaded
right now, not everything it could load - if nothing shows up, load a model in LM Studio
first, then detect again.</div>

<h2>Krea 2 Assistant</h2>
<p>A chat window to whatever model is loaded in LM Studio, scoped to two jobs that feed the
Prompt Builder above:</p>
<ul>
<li><b>Vision</b> mode - attach a reference image and it describes exactly what's in it,
in plain prose, without inventing details or softening explicit content. Good for turning a
reference photo into a starting description.</li>
<li><b>Rewrite</b> mode - paste an existing prompt (from Civitai, another tool, wherever)
and it converts it into Krea 2's format, or applies a specific edit you ask for ("remove the
necklace", "change the pose", "make the scene a rooftop at night" etc).</li>
</ul>
<p>Generation parameters (Temperature / Top P / Max tokens) are ordinary sampling
controls you tune by feel. <b>Context size</b> is different - it's pulled straight from LM
Studio for whichever model is selected, since that's a real property of the running model,
not a free choice.</p>
<p><b>Save Last Response</b> stores the model's last reply - shows up under "Krea 2
Assistant Outputs" on the Saved Prompts tab, ready to reuse or paste into the Prompt
Builder's fields later.</p>
<div class="tip"><b>Tip:</b> a common flow is Vision mode to describe a reference image,
then Save that response, then switch to Rewrite mode and use it as the source prompt to
convert into full Krea 2 format.</div>

<h2>Image Analyser</h2>
<p>Browses a folder of generated images and reads back their embedded generation metadata
(prompt, negative prompt, model, LoRAs, seed, steps, CFG, sampler, scheduler, denoise) -
works for both ComfyUI's own metadata and the standard A1111-style "parameters" text some
save nodes use.</p>
<ul>
<li>The folder tree on the left only shows what's inside whichever folder is selected - it
does not recursively dump every image from every subfolder into one view. Click a
subfolder in the tree to see just its images.</li>
<li>Click a thumbnail for a larger preview plus the parsed metadata; the <b>Raw</b> tab next
to it shows the full, unparsed text exactly as it was embedded in the file, in case
something didn't parse the way you'd expect.</li>
</ul>

<h2>Saved Prompts</h2>
<p>Everything saved from the two tabs above, in one browsable list, split into two labeled
sections: <b>Krea 2 Prompts</b> (from the Prompt Builder) and <b>Krea 2 Assistant Outputs</b>
(from the Assistant's chat). Load sends a Krea 2 Prompts entry back to the Prompt Builder's
fields, or a Krea 2 Assistant entry back into the Assistant's input box. Export writes an
entry out to a .txt file.</p>

<h2>Library Settings</h2>
<p>The tag options that populate the Prompt Builder's four picker fields (medium, shot
size, camera angle, mood) - add, edit, or delete entries here and they show up next time you
open a picker on the Prompt Builder tab. Pinned to the top-right corner rather than sitting
in the main tab row, since it's occasional housekeeping rather than part of the regular
workflow.</p>
"""


class HelpTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        self.browser.setHtml(_HELP_HTML)
        layout.addWidget(self.browser)
