# ComfyUI Prompt Builder

A desktop app for building, refining, and reusing image-generation prompts for **Krea 2** -
powered by a locally-run AI model through [LM Studio](https://lmstudio.ai), with nothing sent
to any cloud service. Everything runs on your own machine.

It grew out of a simple problem: chat-based prompt tools drift and self-contradict the longer
a conversation goes on. This app fixes that by keeping the things you care about (character,
wardrobe, pose, scene, mood, camera, lighting...) as separate, structured fields instead of
one long chat history - correcting one detail means editing that field and regenerating, not
re-explaining the whole image to a model that has to guess what changed.

## What it does

- **Krea 2 Prompt Builder** - build a prompt from structured fields (medium, character,
  wardrobe, pose, scene, shot size, camera angle, lens, camera, aperture, lighting, genre,
  mood) instead of freeform chat. Each free-text field has its own "Vary" button for asking
  the model for a fresh alternative to just that one detail.
- **Krea 2 Assistant** - a focused chat interface with two jobs: describe an attached
  reference image in plain, faithful prose (Vision mode), or rewrite/convert an existing
  prompt - from Civitai, another tool, wherever - into Krea 2's format, including targeted
  edits like "remove the necklace" or "change the pose" (Rewrite mode).
- **Image Analyser** - browse a folder of generated images and read back their embedded
  generation metadata (prompt, negative prompt, model, LoRAs, seed, steps, CFG, sampler,
  scheduler, denoise), for both ComfyUI's own metadata format and the standard A1111-style
  "parameters" text some save nodes use.
- **Saved Prompts** - a browsable history of everything you've saved from the Builder and
  Assistant tabs, ready to reload or export to a text file.
- **Library Settings** - manage the tag vocabulary that populates the Builder's picker
  fields.
- **LM Studio tab** - the single place that manages your connection to LM Studio: detect
  available models, assign which model each tab uses, and load or unload them, all from
  one screen.

Every system prompt the app uses is visible and editable right in the Krea 2 Assistant tab,
with a one-click reset back to the built-in default if you want to undo your own edits.

## Requirements

- **Windows 11** (Windows 10 should work too, but hasn't been tested)
- **Python 3.10 - 3.14**, installed from [python.org](https://www.python.org/downloads/) -
  not the Microsoft Store version
- **[LM Studio](https://lmstudio.ai)**, running locally with its API server turned on, and at
  least one downloaded model (a vision-capable one if you want to use the Assistant's Vision
  mode)

No other accounts, API keys, or cloud services are required - everything runs locally.

## Installing

See **[INSTALLING.md](INSTALLING.md)** for a full walkthrough, including which models to
grab if you don't already have a preference.

The short version: install Python and LM Studio, download a model, then unzip this app and
run `run_promptbuilder.bat`. That script sets up its own private Python environment on first
run - it never touches your system's existing Python install - and launches the app
automatically once it's done. Running the same file again later just opens the app straight
away.


## License

*GPL-3.0 license*
