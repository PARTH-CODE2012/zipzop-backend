# AI Video Editor — Product Definition

**A plain-language description of what the product does, written to be read and approved before any code is designed or written.**

| | |
|---|---|
| **Version** | 0.2 — draft, awaiting approval |
| **Date** | 12 August 2026 |
| **Author** | Maxime Briere · Frontend |
| **For approval by** | Project lead |
| **Built from** | *Next-Gen AI Video Editor — Product Feature Specifications* (PRD) · *System Architecture & Backend Design* (draft) |
| **Status** | Not agreed. 1 position to confirm, 13 open decisions. |

> **What changed since v0.1.** v0.1 asked whether this product is a one-shot generator or a real editor. This version answers that question with a position — it is a real editor — and re-frames everything around it. Section 2 states the position. If the project lead rejects it, most of this document has to be rewritten, so please read section 2 first.

---

## Contents

1. [Purpose of this document](#1-purpose-of-this-document)
2. [The position this document takes](#2-the-position-this-document-takes)
3. [What the product is](#3-what-the-product-is)
4. [What the source documents cover — and what they leave out](#4-what-the-source-documents-cover--and-what-they-leave-out)
5. [How the product works](#5-how-the-product-works)
6. [The editor itself](#6-the-editor-itself)
7. [The AI tools in detail](#7-the-ai-tools-in-detail)
8. [Accounts, credits and limits](#8-accounts-credits-and-limits)
9. [Faces, consent and generated video](#9-faces-consent-and-generated-video)
10. [What the product does not do](#10-what-the-product-does-not-do)
11. [Assumptions we have made](#11-assumptions-we-have-made)
12. [Decisions we need from you](#12-decisions-we-need-from-you)

**How to read the markings**

- 🔵 **Note** — context or an observation worth knowing.
- 🟠 **Decision** — something is undefined and we need an answer.
- 🔴 **Risk** — this could cost us money, time or legal trouble if it is not settled now.

---

## 1. Purpose of this document

We have two documents describing this project: a product feature list, and a backend architecture draft. Between them they describe roughly what we want to build — but they do not agree on scope, and neither states the product behaviour precisely enough to build from.

This document is the missing middle. It says, in ordinary language, **what the product does from the user's point of view**. It contains no technical design: no database tables, no servers, no APIs. That comes next, and only once this is agreed.

Please read it as a proposal. Where the source material was silent, we have either taken a position (marked as such) or asked a question. Every question is collected in section 12.

---

## 2. The position this document takes

Neither source document says what kind of product this is. This document assumes an answer, and everything else follows from it. It is the first thing to confirm or reject.

> ### The product is a video editor. The AI features are tools inside it.
>
> It works the way CapCut, InShot or Premiere Rush work: the user imports footage, lays it out on a timeline, cuts and arranges it, previews it, and exports it. The timeline, the tracks, the playhead, the clips — all of it is there and all of it is under the user's hands.
>
> **What makes it different is the toolbar.** Alongside the ordinary tools — split, trim, transition, text — sit the AI tools: trim the silences automatically, grade the picture, generate the captions, find the best moments, replace a face. The user invokes them when they want them, on the part of the timeline they choose. The result lands *on the timeline*, where it can be adjusted, undone, or thrown away like any other edit.
>
> It is not a machine that swallows a video and returns a finished one. It is an editor whose boring work is done for you.

**What this rules out.** The alternative reading — a one-shot generator where the user uploads footage, picks options, waits, and receives a finished video with no way to adjust it — is what the backend architecture document appears to describe. Under this position, that is not the product. The generator is a much smaller build with a much thinner frontend, so if it is what the project lead actually wants, we need to know now.

🟠 **Decision 1 — everything else depends on this.** Is this an editor with AI tools, or an automatic generator? If it is the editor, sections 5 and 6 describe a substantially larger project than either source document implies, and the backend has to be designed differently from the ground up.

---

## 3. What the product is

A video editing application — used through a website and a mobile app — aimed at creators who publish short vertical video, plus podcasters and streamers cutting long recordings into clips.

The user imports their footage, arranges it on a timeline, and exports a finished video. That much is ordinary. What the product sells is that the tedious parts of that work are done automatically, on demand: the dead air is cut, the picture is graded, the captions are written and timed, the best moments of a long recording are found, the background noise is stripped out. The user stays in control of all of it.

Its signature feature builds a three-dimensional model of a face from three photographs and places that face into a clip, holding it consistent in every frame rather than flickering and distorting the way other tools do.

Ordinary editing happens on the user's device and is free. The AI tools run on our servers and are paid for with **credits**.

🔵 **Who it is for.** The source material points at content creators publishing to TikTok, Instagram Reels and YouTube Shorts, plus podcasters and streamers. It names creator-economy formats directly ("Alex Hormozi style", "Tech Review"). We have assumed this audience throughout. If the real target is agencies, businesses or marketing teams, several decisions below change.

---

## 4. What the source documents cover — and what they leave out

Under the position in section 2, the gap is larger than a disagreement between the two documents. **Neither of them describes the product.**

| | Document 1 — Product features | Document 2 — Backend architecture |
|---|---|---|
| **Calls the product** | Next-Gen AI Video Editor | AI Video Generation & Swap Platform |
| **Describes** | Eight AI features | A face-swap processing pipeline |
| **Describes the editor** | No | No |
| **Has a concept of a project or a timeline** | No | No |
| **Mentions lip sync** | Never | Yes, as a core operation |
| **Face-mapping described as** | "Real-time mapping" | A queued job of about 45 seconds |

Document 1 is a list of AI features with no product around them. Document 2 is a backend for submitting a video and getting a different video back — it stores users, processing tasks, and facial data, and nothing else. There is no project, no timeline, no track, no clip, no edit anywhere in it.

🔴 **Risk — the data model does not fit the product.** Under section 2's position, the central object in this product is a **project**: a saved timeline the user returns to over days, containing clips that reference imported media, with edits layered on top. The architecture document has no such object. It has tasks with an input video URL and an output video URL. This is not a gap that gets filled in later — it is the shape of the whole database. It needs to be resolved before the backend team builds anything.

---

## 5. How the product works

### 5.1 The everyday loop

1. **Start a project.** The user creates a project and imports footage from their device.
2. **Lay it out.** Clips go on a timeline. The user cuts, trims, reorders and arranges them.
3. **Reach for a tool.** Ordinary tools — split, transition, text, volume — work instantly on the device. AI tools are invoked on a selected clip or the whole timeline.
4. **The AI tool does its work.** Some return in a moment. Others go to our servers and take time; the user keeps editing meanwhile.
5. **The result lands on the timeline.** Cuts appear as real cuts. Captions appear as a real text track. A grade appears as an adjustment the user can dial back. Everything is adjustable and everything can be undone.
6. **Export.** The user chooses a format and resolution and gets a finished video file.

### 5.2 The two kinds of AI tool

This distinction matters more than any other in the document, because it determines what the backend must return, how much each tool costs to run, and how much of the result the user can adjust afterwards.

#### Tools that produce *decisions*

They analyse the footage and return **instructions** — where to cut, what words were said and when, which settings to apply. The instructions are applied to the timeline as ordinary edits.

- Smart Trimming — returns a list of cut points
- Captions — returns the transcript with the timing of every word
- Viral Clip Finder — returns the in and out points of the best moments
- Templates & Recommendations — returns a set of settings

Because they return data rather than pixels, they are **cheap to run, quick, and fully editable afterwards**. The user can drag a cut, fix a misheard word, extend a clip. Nothing is baked in.

#### Tools that produce *pixels and sound*

They rebuild the picture or the audio itself. There is no way to express their result as an instruction — the media has to be processed and a new file produced.

- Colour Grading — rewrites every frame's colour
- Voice & Video Enhancer — rebuilds the audio, sharpens and stabilises the picture
- 3D Face Mapping — replaces a face in every frame

These are **slow and expensive**, they must run on our servers, and their result arrives as a new piece of media that replaces the original clip on the timeline. The user can remove them or run them again with different settings, but cannot adjust them the way they can adjust a cut.

🔵 **Why this matters to the backend team.** The first group needs an API that returns structured data quickly. The second needs the queued, GPU-backed pipeline the architecture document already describes. The architecture document currently treats every operation as if it were in the second group.

### 5.3 What happens while a server tool is running

Server-side tools take time — the architecture document estimates around 45 seconds for a face swap, and heavier operations on longer footage will take minutes.

The user must be able to **keep editing while it runs**. They can close the app and come back; the work continues, and the result is waiting for them. A tool that freezes the editor for two minutes is not usable in an editor.

🟠 **Decision 2 — previewing before committing.** If a user applies a colour grade to a ten-minute video, do they wait for the server before seeing anything? Three options: approximate the effect on the device instantly and render properly at export; render a fast low-quality preview on the server; or show nothing until it is done. This affects both how the product feels and how much it costs to run. It needs an answer.

### 5.4 Exporting

At the end, the timeline becomes one video file. Where that happens is an open question:

- **On the device** — free for us, but slow on a phone, and it cannot apply the server-side AI results.
- **On our servers** — consistent, fast, applies everything, but costs money for every export.

Since the pixel-level tools already require server rendering, some hybrid is likely.

🟠 **Decision 3 — where does export happen, and does it cost credits?**

---

## 6. The editor itself

Under section 2's position, this section is the largest part of the product, and it appears in **neither source document**. Everything here is a proposal.

### 6.1 What it must do

| Area | What the user expects |
|---|---|
| **Projects** | Create, name, save, reopen. Work survives closing the app. Autosave — nobody expects to press save in 2026. |
| **Importing** | Bring in video, images and audio from the device. See them in a media bin. |
| **Timeline** | A visible track layout with a playhead, zoomable, showing clips in order with thumbnails and an audio waveform. |
| **Tracks** | At minimum: video, audio, text. Ideally overlays and a music track. |
| **Core edits** | Split a clip, trim its ends, move it, delete it, reorder, duplicate, change its duration. |
| **Preview** | Play the timeline back with everything applied. Scrub with the playhead. Frame-accurate. |
| **Audio** | Per-clip volume, fades, a music track under everything, detach audio from video. |
| **Text** | Add titles and overlays. Position, size, font, colour. Captions arrive here as a track. |
| **Transitions** | Between clips — at minimum cut, fade, dissolve. |
| **Clip adjustments** | Speed, crop, rotate, flip, and the strength of any applied grade. |
| **Undo/redo** | Deep, reliable, and covering AI results too. |
| **Export** | Choose resolution, aspect ratio and quality. Progress. A file at the end. |

### 6.2 The honest assessment

🔴 **Risk — this is the real size of the project, and it is not in the estimate.**

Everything in the table above is table stakes. A user who has used CapCut will notice within thirty seconds if any of it is missing, and will judge the product on it before they ever reach an AI feature. It is also, on its own and with no AI at all, a large piece of software. CapCut, InShot and Premiere Rush are each the work of substantial teams over years.

Building a competent timeline editor **and** eight AI features **and** a face-mapping pipeline is not an MVP. Something has to give. We would rather say this now than discover it at the deadline.

Three ways to make it fit — please pick one:

1. **Cut the editor down.** Ship a deliberately simple timeline: one video track, one audio track, one text track, basic cuts. Accept that it is less capable than CapCut and compete on the AI tools instead.
2. **Cut the AI features down.** Build a proper editor and ship with two or three AI tools — the ones that differentiate us — adding the rest over following releases.
3. **Extend the timeline.** Build both properly and accept a launch date months later than currently assumed.

🟠 **Decision 4 — which of the three?** This is a scheduling and budget decision, not a technical one, and the development team cannot make it.

### 6.3 Platforms

A timeline editor is not a screen — it is an interaction model, and it has to be built separately for the web and for touch. A web version and a mobile version share almost no interface code.

🟠 **Decision 5 — web, iOS, Android: which, and all at launch?** This is the single largest variable in the frontend estimate, and neither document mentions platforms at all.

---

## 7. The AI tools in detail

Each tool below is written as behaviour: what the user does, what the system does, and — under section 2's position — **what it puts on the timeline** and how much of it the user can change afterwards. Numbering matches the original PRD so the two can be read side by side.

---

### Feature 01 — Smart Trimming

*Returns decisions · fast · fully editable*

Removes the parts of a recording where nothing is happening or something went wrong, so a rambling take becomes a tight one.

| | |
|---|---|
| **User does** | Selects a clip on the timeline and applies Smart Trim. |
| **System does** | Listens to the audio and finds four things: silences and long pauses, filler sounds ("um", "uh"), stutters and false starts, and sentences said twice because the speaker retook the line. |
| **Puts on the timeline** | The clip is split at every cut point and the dead segments are removed — as ordinary edits. |
| **User can then** | Drag any cut, restore a removed segment, or undo the whole thing in one step. |

**Needs confirming**

1. Is there a strength setting — light, medium, aggressive — or one fixed behaviour?
2. Does the user get a review step before the cuts are applied, or are they applied straight away and adjusted afterwards? (We propose the latter — it fits the editor model.)
3. What is the longest clip we accept?
4. Does it handle more than one speaker, e.g. an interview or a two-person podcast?

🟠 **The headline claim does not match the feature.** The PRD promises "a 10-minute rough recording into a punchy 2-minute video". That is an 80% reduction. Removing pauses and stumbles from a normal recording saves roughly 10–25%. To get from ten minutes to two, the system would have to *decide which content is worth keeping* — a much harder and quite different feature, closer to Feature 03. Please confirm which we are promising.

---

### Feature 02 — Cinematic Colour Grading

*Returns pixels · slow · adjustable strength, not adjustable detail*

Makes footage shot on a phone look like it was shot deliberately.

| | |
|---|---|
| **User does** | Selects a clip, picks a look — the PRD names Cinematic, VLOG and Cyberpunk — or lets the system choose. |
| **System does** | Examines the lighting, exposure and colour balance of the footage and applies a colour profile tuned to what it found, rather than the same flat filter regardless of scene. |
| **Puts on the timeline** | A grade attached to the clip, shown as a badge on it. |
| **User can then** | Change the look, dial the strength up or down, or remove it. Not edit it frame by frame. |

**Needs confirming**

1. Exactly which looks ship at launch? Three is a thin catalogue; competitors offer dozens.
2. Can users bring their own colour profile (a LUT file), or is the list fixed?
3. One grade for a whole clip, or a different grade per scene when the lighting changes?
4. Is the grade previewed instantly on the device, or only after a server render? (See decision 2.)

---

### Feature 03 — Viral Clip Finder

*Returns decisions · fast · fully editable*

Takes a long recording and pulls out the moments most likely to perform as standalone short videos.

| | |
|---|---|
| **User does** | Imports a long-form video — a podcast episode, a stream, a webinar — and asks for clips. |
| **System does** | Scans the recording for signs of a high point: raised voices, laughter, changes in pitch, stretches of fast dense speech. Picks the strongest moments. |
| **Puts on the timeline** | Each moment becomes its own short project, ready to edit — trimmed to the moment and set to a vertical 9:16 frame. |
| **User can then** | Extend or shorten any clip, discard the ones it got wrong, edit each one normally. |

**Needs confirming**

1. How many clips come out of one video, and does the user choose the number?
2. How long should a clip be — 15, 30, 60 seconds? Fixed or variable?
3. What is the longest recording we accept? A three-hour stream is a large and expensive piece of processing.
4. Do the clips arrive already captioned and graded, or does the user apply those afterwards?

🔴 **A whole capability is missing from both documents.** Turning a landscape recording into a vertical clip means throwing away about two-thirds of the width of the picture. There are only two ways: crop the middle and accept that people sitting off-centre get cut in half, or **track the speaker and move the crop to follow them**, switching between speakers as they talk. Speaker tracking is what every competing product does, it is substantial work, and it appears nowhere in either document. A centre-crop of a two-person podcast produces unusable clips.

---

### Features 04 & 05 — Templates and the Recommendation Engine

*Returns decisions · fast · fully editable*

A template is a saved combination of caption style, transitions, pacing, colour grade and music that together reproduce a recognisable style of video.

| | |
|---|---|
| **User does** | Applies a template to a project, or accepts the one the system suggests. |
| **System does** | Applies the whole set of settings at once. Separately, reads the mood and pace of the footage and suggests the template, transitions and grade most likely to suit it. |
| **Puts on the timeline** | All of it as ordinary, separate edits — a caption style, transitions between clips, a grade, a music track. |
| **User can then** | Change any individual part without losing the rest. A template is a starting point, not a lock. |

**Needs confirming**

1. Who designs the templates, and how many exist on day one?
2. Can users save their own template, or a brand kit — logo, fonts, colours — and reuse it?
3. What is the system reading when it judges "mood"? Speech tone, pace, the words themselves, the picture?

🔴 **Two commercial problems hiding inside this feature.**

- **Music.** If templates include background music, we need a licensed music library. That is a commercial agreement with a per-track or per-user cost, not a development task, and nobody has mentioned it. If templates do not include music, the style they reproduce will feel incomplete.
- **Naming templates after real people.** "Alex Hormozi style" uses a living person's name to sell a product feature — a trademark and personality-rights exposure with no upside. We recommend descriptive names ("High-energy talking head", "Tech review") and keeping the reference out of the product.

---

### Feature 06 — Voice and Video Enhancer

*Returns pixels and audio · slow · the most expensive tool in the product*

Repairs footage recorded in imperfect conditions.

| | |
|---|---|
| **User does** | Selects a clip and asks for it to be cleaned up. |
| **System does** | Four separate improvements: removes background noise, removes room echo, sharpens soft or blurry picture, steadies shaky camerawork. |
| **Puts on the timeline** | The clip's media is replaced with the cleaned version. |
| **User can then** | Revert to the original. The original is never destroyed. |

**Needs confirming**

1. Is this one button, or four switches the user controls independently? (We propose four — a user with good picture and bad audio should not pay for both.)
2. What resolution does sharpening aim for — 1080p, 4K?
3. Stabilisation normally zooms in slightly and loses the edges of the frame. Is that acceptable, and does the user need to be told?

🟠 **This is the most expensive feature in the product.** Sharpening video means processing every individual frame — around 18,000 of them in a ten-minute video. It is by far the heaviest use of our processing hardware and can cost more to run than everything else combined. Decide now whether it is limited to short clips, reserved for a paid tier, or priced at a credit cost that reflects what it really costs us.

---

### Features 07 & 08 — Automatic Captions and Animated Text

*Returns decisions · fast · fully editable*

Puts the spoken words on screen as animated subtitles, timed to the speech, with emphasis on words said forcefully.

| | |
|---|---|
| **User does** | Asks for captions on a clip or the whole project, and picks a style. |
| **System does** | Transcribes the speech, works out when each individual word is spoken, and detects which words were emphasised. |
| **Puts on the timeline** | A text track, word by word, timed to the audio. |
| **User can then** | **Correct any word.** Move captions, restyle them, retime them, delete a line. |

**Needs confirming**

1. "30+ languages" — which ones exactly? And does it mean transcribing the language spoken, translating into other languages, or both? These are different features.
2. Are captions burned permanently into the picture at export, or also delivered as a separate subtitle file the platform can switch on and off?
3. How many caption styles, and can the user control position, size and colour? Captions placed where TikTok puts its own interface get covered up.
4. In a two-person conversation, do we label who is speaking?

🔵 **This is the tool that proves the editor model is right.** Speech recognition always mis-hears names, brands and technical terms. Under a one-shot generator, a customer's name burned into the video in large animated letters, spelled wrong, is unfixable. In an editor, it is a two-second correction. This feature alone justifies section 2's position.

---

### Core differentiator — 3D Consistent Face Mapping

*Returns pixels · slow · the product's signature feature*

Places a face into video and keeps it stable in every frame, instead of the flickering and distortion other tools produce.

| | |
|---|---|
| **Set up, once** | The user uploads three photographs of a face — left profile, straight on, right profile. The system builds a three-dimensional model of it (shape, skin, how light falls across it) and saves it to their account as a reusable face profile. |
| **Each time they use it** | The user selects a clip and one of their saved face profiles. The system replaces the face of the person in that clip, frame by frame, following the head as it turns and keeping the original expressions. |
| **Puts on the timeline** | The clip's media is replaced with the mapped version. |
| **User can then** | Revert to the original, or re-run with a different face profile. |

**Needs confirming — the most consequential questions in this document**

1. **Whose video is the target?** Only footage the user filmed of themselves? Any video they choose to import, including video of other people? Or a library of ready-made videos we supply? These are three different products with three very different levels of legal exposure. The middle option is, plainly, a face-swap tool.
2. **Whose face can be uploaded?** Only the account holder's own face, verified somehow? Or any three photographs of anyone?
3. **Is lip sync part of the product?** The architecture document lists it as a core operation. The PRD never mentions it. Lip sync means making a person on screen appear to say words they did not say. That needs an explicit yes or no, not an inherited assumption.
4. The PRD says "real-time mapping". The architecture document describes a queued job of about 45 seconds. We have assumed "real-time" was meant loosely, as "automatic". Please confirm we are not promising a live preview on the timeline — that would be a fundamentally different and far more expensive system.

---

## 8. Accounts, credits and limits

The architecture document establishes that each user has an account with an email and password, and a balance of credits. Every job spends credits. That is the whole of what we know.

Under section 2's position, one thing becomes clearer: **ordinary editing should be free**. A user who is charged for splitting a clip will not use the product. Credits pay for the server-side AI tools, and possibly for export.

Everything below is undefined in both documents.

| Area | What is missing |
|---|---|
| **Pricing** | What each tool costs in credits. Sharpening a 4K video and adding captions to a 30-second clip cannot cost the same. |
| **Buying credits** | How users buy them, through which payment provider, in what bundles. **There is no payment system anywhere in the architecture document.** |
| **Free tier** | Whether new users get free credits, how many, and what they can do with them. |
| **Subscriptions** | Whether there are monthly plans as well as credits, and what a plan includes. |
| **Failed jobs** | Whether credits are refunded when a tool fails through no fault of the user. We recommend yes, automatically. |
| **Export** | Whether exporting costs credits, and whether free exports carry a watermark. |
| **Upload limits** | Maximum file size, video length, resolution, and which formats we accept. |
| **Storage** | How long we keep a user's media and projects before deleting them. A direct and recurring cost, and under this position we are storing whole projects, not just finished videos. |
| **Teams** | Whether an account is one person or a company with several members sharing a balance. |
| **Sign-in** | Whether users can sign in with Google or Apple, or only email and password. |

🟠 **Decision 6 — someone needs to own the commercial model.** It is not a backend question, but the backend cannot be finished without it.

---

## 9. Faces, consent and generated video

This section is not in either source document, and it is the one we would most strongly urge you to read.

A 3D model of someone's face is **biometric data**. Under European data protection law and its equivalents elsewhere, that is a special category of personal data with stricter rules than an email address: it needs explicit consent, a stated purpose, a defined retention period, and it must be deletable on request. The architecture document stores facial data with none of this addressed.

Separately, video in which a real person's face has been altered is **synthetic media**, and a growing number of jurisdictions now require it to be marked as such.

These are not legal footnotes for later. They are product behaviour somebody has to build, which means they need specifying here, alongside the features:

- **A consent step** when a face is uploaded — the user states whose face it is and confirms they have the right to use it — and a record of that consent.
- **Deletion that actually deletes** — a way to remove a face profile and everything derived from it, and to know it is gone. Under this position that includes every project the face was used in.
- **Marking generated video** — a visible watermark, invisible provenance data embedded in the file, or both. A visible watermark on free output is also a business decision, not only a compliance one.
- **A position on misuse** — a face-mapping tool that accepts imports of other people's videos will be used to make things we do not want to be associated with. We need to decide what we block, how we detect it, how someone reports it, and what happens then.
- **Retention** — how long face data survives after a user stops using the product or closes their account.

🔴 **Decision 7 — this needs an owner, not just an answer.** None of it can be decided by the development team, and none of it can be bolted on at the end. We recommend a legal opinion on the face-mapping feature specifically, in the markets we intend to launch in, **before** the architecture is finalised — the answer may change what we are allowed to store and for how long, which changes the design.

---

## 10. What the product does not do

Neither document describes any of the following. We propose they are all **out of scope for version 1**. If you disagree with any line, that is a scope change and we need to know now.

| Not building | Note |
|---|---|
| **Publishing to TikTok, YouTube or Instagram** | The PRD talks about "Reels and Shorts growth", which implies it — but publishing directly to those platforms is real work with each platform's approval process attached. **Please confirm explicitly.** |
| **Collaboration** | No sharing a project with a teammate, no comments, no review. |
| **Multi-camera editing** | One angle at a time. |
| **Keyframed animation** | No custom motion paths or property animation over time. |
| **Green screen / background removal** | Not mentioned anywhere, commonly expected in this category. Flagging it deliberately. |
| **A stock footage or b-roll library** | Users bring their own footage. |
| **AI voiceover or voice cloning** | The product cleans up the voice in the recording. It does not generate one. |
| **Script or content generation** | No idea generation, no writing, no titles or descriptions. |
| **Analytics on published videos** | We do not track how a video performed after it left us. |
| **Working offline** | Ordinary editing could work offline; the AI tools cannot. We propose the app requires a connection throughout. |

---

## 11. Assumptions we have made

Where the source documents were silent, we filled the gap to write this document. Each of these could be wrong, and several carry real work behind them.

1. The audience is individual content creators publishing short vertical video, plus podcasters and streamers.
2. Ordinary editing is free and runs on the device; only the AI tools cost credits.
3. The original media is never destroyed — every AI result can be reverted.
4. "Real-time" in the PRD means "automatic, without manual work", not "live on screen as it happens".
5. AI tools can be combined freely on one project — a user can trim, grade and caption the same footage.
6. The interface is in English at launch. The "30+ languages" refers to speech in users' videos, not to the app's own interface.
7. Projects and media are private to the user by default; nothing is public or shared unless they publish it themselves.
8. The claimed figures in the PRD — 98% transcription accuracy, 80% retention improvement — are marketing statements, not targets the system will be measured against. If they are meant as commitments, we need to say so now: guaranteeing 98% accuracy across 30 languages is a very different undertaking.

---

## 12. Decisions we need from you

Ordered by urgency. The five marked **Blocking** stop the technical design from starting at all.

| # | Decision | Why it matters | When |
|---|---|---|---|
| **1** | Is this an editor with AI tools, or an automatic generator? | Section 2's position. Everything in this document follows from it, and the two answers need different databases, different pipelines and very different amounts of frontend work. | 🔴 **Blocking** |
| **2** | How do we fit a timeline editor *and* eight AI features *and* face mapping into one release? Cut the editor, cut the features, or extend the timeline? | The editor alone is a large product and appears in neither source document. Something has to give, and it is a budget decision. | 🔴 **Blocking** |
| **3** | Whose video can a face be mapped onto — only the user's own, anything they import, or a library we supply? | Determines what product we are actually selling and how much legal exposure it carries. | 🔴 **Blocking** |
| **4** | Who owns consent, watermarking and misuse policy, and when do we get legal advice? | Facial data is regulated. The answers may change what we are allowed to store, which changes the architecture. | 🔴 **Blocking** |
| **5** | Web, iOS, Android — which, and all at launch? | A timeline editor has to be built separately for web and for touch. The single largest variable in the frontend estimate. | 🔴 **Blocking** |
| **6** | Is lip sync in the product? | It appears in the architecture document and nowhere else. A significant feature and a significant risk to inherit by accident. | 🟠 Before design |
| **7** | Does Smart Trimming tighten a recording, or cut it down to its best parts? | The stated 10-minutes-to-2 result is a different, harder feature than removing pauses. | 🟠 Before design |
| **8** | Does the Clip Finder track speakers when converting to vertical? | Without it, clips of more than one person are unusable. Substantial work, in neither document. | 🟠 Before design |
| **9** | How does the user preview a server-side effect before committing to it? | Affects how the product feels and how much it costs to run. | 🟠 Before design |
| **10** | Where does export happen — device or server — and does it cost credits? | Changes the cost model and the shape of the export pipeline. | 🟠 Before design |
| **11** | What does each tool cost in credits, and how are credits bought? | There is no payment system in the architecture at all. | 🟠 Before design |
| **12** | Do templates include music, and who licenses it? | A commercial agreement with a cost attached, not a development task. | ⚪ Before build |
| **13** | Do we publish directly to TikTok, YouTube and Instagram? | Implied by the PRD but never specified. Each platform has its own approval process. | ⚪ Before build |

---

## What we are asking for

Please read **section 2** first — it states the position this whole document rests on, and if it is wrong, everything after it needs rewriting. Then **section 6.2**, which is the honest assessment of how big this project actually is.

Answering the five blocking decisions is enough for us to start the technical design. The remaining eight can follow within the week.

Corrections to anything else here are welcome and expected. This is a proposal, not a finished plan, and it is easier to change now than at any point after.

---

*AI Video Editor · Product Definition v0.2 · 12 August 2026*
*Compiled from "Next-Gen AI Video Editor — Product Feature Specifications & Backend Requirements" and "System Architecture & Backend Design" (draft).*
*Status: draft for approval. Not agreed. No part of this document should be treated as a committed requirement until the decisions in section 12 are answered.*
