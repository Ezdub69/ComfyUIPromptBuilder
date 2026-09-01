# Installing ComfyUI Prompt Builder on Windows 11

(Windows 10 should also work, but hasn't been tested.)

## Step 1 - Get the extra files you'll need

Besides the `ComfyUIPromptBuilder.zip` file from the release page, you'll need a couple of
other things installed first: Python, and LM Studio with a couple of model files.

### Install Python

Use the official installer from python.org - **not** the Microsoft Store version, and not
Python's separate "install manager" app.

Recommended: Python 3.14, downloaded directly from python.org. The app has also been tested
on 3.13. Any version up to the latest 3.14.x release should work fine, for example:

- https://www.python.org/ftp/python/3.14.5/python-3.14.5-amd64.exe
- https://www.python.org/ftp/python/3.14.7/python-3.14.7-amd64.exe

**During installation, make sure "Add Python to PATH" is checked.**

### Install LM Studio

Download LM Studio from https://lmstudio.ai/download. There are two versions offered for
Windows on that page - pick the regular one, not the Bionic build.

Once downloaded, install and run it, go through the setup screens, and turn on Developer
Mode. Then quit LM Studio for now - you'll come back to it in a moment.

### Download the two model files

This guide uses one caption/vision model, downloaded as two separate files:

- **Prompt Builder and Rewrite model:**
  `Qwen3-VL-8B-NSFW-Caption-V4.5-heretic.i1-Q4_K_S.gguf`
  https://huggingface.co/mradermacher/Qwen3-VL-8B-NSFW-Caption-V4.5-heretic-i1-GGUF/blob/main/Qwen3-VL-8B-NSFW-Caption-V4.5-heretic.i1-Q4_K_S.gguf

- **Vision projector file:**
  `mmproj-Qwen3-VL-8B-Instruct-F16.gguf`
  https://huggingface.co/lmstudio-community/Qwen3-VL-8B-Instruct-GGUF/blob/main/mmproj-Qwen3-VL-8B-Instruct-F16.gguf

Together they're around 6 GB. Once both are downloaded, place them in your LM Studio models
folder - normally:

```
C:\Users\<your username>\.lmstudio\models
```

(Your main OS drive → Users → your username → `.lmstudio` → `models`.)

Inside the `models` folder, create a folder named `mradermacher`, and put both downloaded
files into it.

### Turn on LM Studio's local server

Start LM Studio again. On the left-hand sidebar, click the icon that looks like `>_`
(the Developer tab). At the top, open the server settings and turn on **Enable CORS**, then
switch the server **Status** to on.

You don't need to manually load any models in LM Studio itself - once the app is installed,
it can detect and load models for you (see Step 3).

## Step 2 - Install the app

Unzip `ComfyUIPromptBuilder.zip`. This gives you a folder called `ComfyUIPromptBuilder`.

Open that folder and run `run_promptbuilder.bat`.

This sets up the app in its own private Python environment - none of your system's existing
Python installation or files are touched. Once setup finishes, the app starts automatically.

To open the app again later, just run `run_promptbuilder.bat` the same way - it launches
straight away once it's already installed.

## Step 3 - First-time setup inside the app

If everything installed correctly, you'll see several tabs, including a Help tab with tips on
how to use the app.

The first thing to do is open the **LM Studio** tab (it should already be selected the first
time you start the app). In the server address field, it should say:

```
http://localhost:1234
```

This is correct if LM Studio is running on the same PC as this app, which is the case for
most people. (If you're going to run LM Studio on a different networked PC instead, you can
change this address later - see the Help tab.)

With that confirmed, click **Detect Models**. This asks LM Studio which models it has
available. Then, in the Model Assignments section, set all three to the model you downloaded:

- **Krea 2 Prompt Builder:** `Qwen3-VL-8B-NSFW-Caption-V4.5-heretic.i1-Q4_K_S.gguf`
- **Krea 2 Assistant - Vision:** `Qwen3-VL-8B-NSFW-Caption-V4.5-heretic.i1-Q4_K_S.gguf`
- **Krea 2 Assistant - Rewrite:** `Qwen3-VL-8B-NSFW-Caption-V4.5-heretic.i1-Q4_K_S.gguf`

Then click **Load Assigned Models**. Once it's done, it'll show all three as loaded.

> **Note:** using the app again after something like a system reboot, you'll need LM Studio
> running again first. The app checks automatically on startup and will tell you if your
> assigned models aren't loaded any more - if so, just click **Load Assigned Models** again.

You can check the Krea 2 Prompt Builder and Krea 2 Assistant tabs too, which will also show
the models as loaded.

## Quick test

Once everything's showing as loaded, try this on the **Krea 2 Assistant** tab: attach any
image you have, then just click **Send** - you don't need to type anything first. It should
come back with a description of the image if everything's working correctly.

It may take a moment to respond - that depends on your PC's specs, GPU, and RAM.

## A few closing notes

- You're not limited to the models in this guide - plenty of others work too. The ones listed
  here are just the combination that's worked well through testing.
- The models won't produce a perfect prompt every time - expect to tweak the results to your
  liking. Even so, it takes care of most of the heavy lifting of writing a prompt from
  scratch.
- The prompts this app produces aren't limited to ComfyUI - they work anywhere that accepts a
  text prompt for image generation, including other UIs and websites like Forge.
