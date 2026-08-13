# AI Video Editor — Product Vision

**A plain-language description of what the product does. The position and the five blocking decisions are now settled; this document records what was decided and what remains open.**

| | |
|---|---|
| **Version** | 1.0 — position approved by the project lead |
| **Date** | 12 August 2026 |
| **Author** | MMaxouB · Frontend |
| **Approved by** | Project lead, 12 August 2026 |
| **Supersedes** | `vision.md` v0.2 (draft) |
| **Status** | Position agreed. 5 blocking decisions answered, 8 open, 3 new questions raised by the answers. |

> **What changed since v0.2.** v0.2 proposed a position — that this is a real editor, not a one-shot generator — and asked five blocking questions. The project lead confirmed the position and answered all five. This version folds those answers in and marks what they now commit us to. The parts of the document that were never in question are unchanged.

---

## Contents

1. [Purpose of this document](#1-purpose-of-this-document)
2. [The position, now confirmed](#2-the-position-now-confirmed)
3. [What the product is](#3-what-the-product-is)
4. [What the answers commit us to](#4-what-the-answers-commit-us-to)
5. [How the product works](#5-how-the-product-works)
6. [The editor itself](#6-the-editor-itself)
7. [The AI tools in detail](#7-the-ai-tools-in-detail)
8. [Accounts, credits and limits](#8-accounts-credits-and-limits)
9. [Faces, consent and generated video](#9-faces-consent-and-generated-video)
10. [What the product does not do](#10-what-the-product-does-not-do)
11. [Assumptions we have made](#11-assumptions-we-have-made)
12. [Decision register](#12-decision-register)

**How to read the markings**

- 🟢 **Decided** — answered by the project lead. Build to this.
- 🔵 **Note** — context or an observation worth knowing.
- 🟠 **Open** — still undefined. Needs an answer.
- 🔴 **Risk** — this could cost us money, time or legal trouble if it is not settled.

---

## 1. Purpose of this document

We started with two source documents: a product feature list (the PRD) and a backend architecture draft. Between them they described roughly what we wanted to build, but they did not agree on scope, and neither stated product behaviour precisely enough to build from.

This document is the missing middle. It says, in ordinary language, **what the product does from the user's point of view**. It contains no technical design — that lives in [`03-backend-architecture.md`](03-backend-architecture.md) and [`04-frontend-architecture.md`](04-frontend-architecture.md), both of which follow from this one.

For the short version of what is being built first, read [`02-scope-v1.md`](02-scope-v1.md) instead. It is the document the development team should open first.

---

## 2. The position, now confirmed

🟢 **Decided.** The project lead confirmed this on 12 August 2026: *"It's definitely an editor (CapCut style). AI tools will sit right inside the toolbar."*

> ### The product is a video editor. The AI features are tools inside it.
>
> It works the way CapCut, InShot or Premiere Rush work: the user imports footage, lays it out on a timeline, cuts and arranges it, previews it, and exports it. The timeline, the tracks, the playhead, the clips — all of it is there and all of it is under the user's hands.
>
> **What makes it different is the toolbar.** Alongside the ordinary tools — split, trim, transition, text — sit the AI tools: trim the silences automatically, grade the picture, generate the captions, find the best moments, replace a face. The user invokes them when they want them, on the part of the timeline they choose. The result lands *on the timeline*, where it can be adjusted, undone, or thrown away like any other edit.
>
> It is not a machine that swallows a video and returns a finished one. It is an editor whose boring work is done for you.

Everything else in this document, and both architecture documents, rest on this.

---

## 3. What the product is

A video editing application aimed at creators who publish short vertical video, plus podcasters and streamers cutting long recordings into clips. **Web first**, with mobile to follow.

The user imports their footage, arranges it on a timeline, and exports a finished video. That much is ordinary. What the product sells is that the tedious parts of that work are done automatically, on demand: the dead air is cut, the picture is graded, the captions are written and timed, the best moments of a long recording are found, the background noise is stripped out. The user stays in control of all of it.

Its signature feature builds a three-dimensional model of a face from three photographs and places that face into a clip, holding it consistent in every frame rather than flickering and distorting the way other tools do — with lip sync so the result matches the audio.

Ordinary editing happens in the browser and is free. The AI tools run on our servers and are paid for with **credits**.

🔵 **Who it is for.** The source material points at content creators publishing to TikTok, Instagram Reels and YouTube Shorts, plus podcasters and streamers. We have assumed this audience throughout. If the real target is agencies, businesses or marketing teams, several decisions below change.

---

## 4. What the answers commit us to

The project lead answered all five blocking questions. Here is what each answer settles — and, where an answer opens something new, what it opens.

### 4.1 Editor, not generator

*"It's definitely an editor (CapCut style)."*

🟢 Settled. See section 2. This is the largest single commitment in the project: a timeline editor appears in **neither source document**, and it is the biggest part of the build.

### 4.2 Phased release

*"Let's go with a phased release… ship a clean basic editor + 2-3 core AI tools first to get it live and start generating revenue, then roll out the rest incrementally."*

🟢 Settled in principle — this is option 2 from v0.2 section 6.2, and it is the right call.

🟠 **New question raised: which 2-3 tools?** The answer does not name them, and the choice materially changes the backend: a release built around captions and trimming needs a fast analysis queue and no GPU inference at all, while a release built around face mapping needs a GPU cluster, facial data storage and a consent flow from day one.

[`02-scope-v1.md`](02-scope-v1.md) proposes a selection with reasoning. **It is a proposal, not a decision** — the project lead should confirm or change it before the backend team starts.

### 4.3 Face mapping targets both own and imported footage

*"Both A & B. Users should be able to use their own footage as well as imported clips. We'll add a legal disclaimer/consent checkbox on upload to cover compliance."*

🟢 Settled as product behaviour. The user can map a face onto footage they filmed **and** onto any clip they import.

🔴 **This is the option with the most legal exposure, and the proposed mitigation is lighter than the exposure.** Recording this plainly, not to reopen the decision, but so nobody is surprised later:

- A checkbox is a record that the user *asserted* they had the right. It is not verification, and under GDPR a face model of a **third party** is that person's biometric data, not the uploader's — the uploader cannot consent on their behalf by ticking a box.
- Several jurisdictions require generated or altered video to be marked as synthetic. A checkbox does not address that at all.
- A tool that accepts arbitrary imported video **will** be used for non-consensual content. We need a stated position on what we block, how it is reported, and what happens then.

**Agreed handling:** this work is deferred, not cancelled. The v1 scope does not include face mapping (see [`02-scope-v1.md`](02-scope-v1.md)), which buys time to settle it properly while the editor is being built. Section 9 lists what still has to be decided, and it needs an owner before the face-mapping phase starts — not before v1 ships.

### 4.4 Web first

*"Web first. We'll launch on Web, get revenue flowing, and then reuse the same backend architecture for iOS/Android down the road."*

🟢 Settled. One frontend to build, not three.

🔵 **The backend reuse assumption is sound, and we should protect it.** The API is the reusable part; the editing interface is not. To keep the promise real, the backend must not grow browser-specific assumptions — no HTML in responses, no session state that assumes cookies, no rendering that depends on what a browser can do. [`05-api-contract.md`](05-api-contract.md) is written to be consumed by a native mobile client as readily as by the web app.

### 4.5 Lip sync is in

*"Yes, we need lip sync too along with face mapping so the generated video looks natural with the audio."*

🟢 Settled. Lip sync ships alongside face mapping — not in v1, but in the phase that delivers the differentiator.

🔵 **Worth being precise about what was agreed**, because the phrase "looks natural with the audio" admits two readings:

| Reading | What it means | Difficulty |
|---|---|---|
| **Re-sync** — the one we have assumed | After a face is mapped onto a clip, the new mouth moves in time with the **existing** audio, so the swap does not look dubbed. | Hard, but it is part of doing face mapping properly. |
| **Re-voice** | The person is made to say **new** words they never said, driven by new audio. | A different feature, far more sensitive, and much closer to what people mean by "deepfake". |

We have scoped the first. If the second was intended, say so — it changes the model, the cost and the legal picture.

---

## 5. How the product works

### 5.1 The everyday loop

1. **Start a project.** The user creates a project and imports footage.
2. **Lay it out.** Clips go on a timeline. The user cuts, trims, reorders and arranges them.
3. **Reach for a tool.** Ordinary tools — split, transition, text, volume — work instantly in the browser. AI tools are invoked on a selected clip or the whole timeline.
4. **The AI tool does its work.** It runs on our servers and takes time; the user keeps editing meanwhile.
5. **The result lands on the timeline.** Cuts appear as real cuts. Captions appear as a real text track. A grade appears as an adjustment the user can dial back. Everything is adjustable and everything can be undone.
6. **Export.** The user chooses a format and resolution and gets a finished video file.

### 5.2 The two kinds of AI tool

This distinction matters more than any other in the document. It determines what the backend must return, how much each tool costs to run, and how much of the result the user can adjust afterwards. The whole backend design in [`03-backend-architecture.md`](03-backend-architecture.md) is built on it.

#### Tools that produce *decisions*

They analyse the footage and return **instructions** — where to cut, what words were said and when, which settings to apply. The instructions are applied to the timeline as ordinary edits.

- Smart Trimming — returns a list of cut points
- Captions — returns the transcript with the timing of every word
- Colour Grading — returns which colour profile to apply and how strongly
- Viral Clip Finder — returns the in and out points of the best moments
- Templates & Recommendations — returns a set of settings

Because they return data rather than pixels, they are **cheap to run, quick, and fully editable afterwards**. The user can drag a cut, fix a misheard word, extend a clip. Nothing is baked in until export.

#### Tools that produce *pixels and sound*

They rebuild the picture or the audio itself. There is no way to express their result as an instruction — the media has to be processed and a new file produced.

- Voice & Video Enhancer — rebuilds the audio, sharpens and stabilises the picture
- 3D Face Mapping and Lip Sync — replaces a face in every frame

These are **slow and expensive**, they must run on GPU hardware, and their result arrives as a new piece of media that sits on the timeline in place of the original. The user can remove them or run them again with different settings, but cannot adjust them the way they can adjust a cut.

🔵 **Colour grading moved between the two groups since v0.2**, and it is worth explaining why, because it saves real money. A colour grade is a lookup table applied to each frame. The *analysis* — looking at the footage and deciding which profile suits it — is cheap and returns a few numbers. The *application* can be done in the browser on the preview, instantly and for free, and baked properly only at export, which we are rendering server-side anyway. So grading needs no GPU job of its own. Only the analysis is a job.

### 5.3 What happens while a tool is running

Server-side tools take time — seconds for analysis, minutes for anything that rebuilds pixels.

The user must be able to **keep editing while it runs**. They can close the tab and come back; the work continues, and the result is waiting for them. A tool that freezes the editor for two minutes is not usable in an editor.

### 5.4 Previewing and exporting

🟢 **Resolved by the architecture** (this was open decision 9 and 10 in v0.2):

- **Preview** happens in the browser, on a lightweight proxy copy of the footage generated when it was uploaded. Grades, captions, transitions and transforms are composited live on screen. Nothing is sent to a server to see what an edit looks like.
- **Export** happens on our servers. It is the one place where the real, full-resolution media is assembled with every edit baked in. This gives one consistent render path, it is the same pipeline the pixel-level AI tools use, and it does not depend on what a given browser can do.

Details in [`03-backend-architecture.md`](03-backend-architecture.md) §6 and [`04-frontend-architecture.md`](04-frontend-architecture.md) §4.

🟠 **Open:** whether exporting costs credits, and whether free-tier exports carry a watermark. Both are commercial decisions.

---

## 6. The editor itself

This is the largest part of the product, and it appears in **neither source document**. Everything here is our proposal.

### 6.1 What it must do

| Area | What the user expects |
|---|---|
| **Projects** | Create, name, save, reopen. Work survives closing the tab. Autosave — nobody expects to press save in 2026. |
| **Importing** | Bring in video, images and audio. See them in a media bin. |
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

### 6.2 Why the phased release was the right answer

Everything in the table above is table stakes. A user who has used CapCut will notice within thirty seconds if any of it is missing, and will judge the product on it before they ever reach an AI feature. It is also, on its own and with no AI at all, a large piece of software.

Building a competent timeline editor **and** eight AI features **and** a face-mapping pipeline in one release was never realistic. The phased approach the project lead chose means the editor gets built properly while the AI surface stays small, and each later phase adds tools onto machinery that already works.

🔵 **One consequence worth naming.** Phase 1 ships without face mapping, which is the product's differentiator. That is the correct engineering order — but it means **v1 cannot be marketed as the face-swap product**. Its story has to be "the editor that does the boring work for you". If marketing needs the differentiator at launch, that is a scope conversation to have now, not in three months.

---

## 7. The AI tools in detail

Each tool is written as behaviour: what the user does, what the system does, what it puts on the timeline, and how much of it the user can change afterwards. Numbering matches the original PRD so the two can be read side by side. Which phase each tool lands in is in [`02-scope-v1.md`](02-scope-v1.md).

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

**Still to confirm**

1. Is there a strength setting — light, medium, aggressive — or one fixed behaviour?
2. Does the user get a review step before the cuts are applied, or are they applied straight away and adjusted afterwards? (We propose the latter — it fits the editor model.)
3. What is the longest clip we accept?
4. Does it handle more than one speaker, e.g. an interview or a two-person podcast?

🟠 **The headline claim does not match the feature.** The PRD promises "a 10-minute rough recording into a punchy 2-minute video". That is an 80% reduction. Removing pauses and stumbles from a normal recording saves roughly 10–25%. To get from ten minutes to two, the system would have to *decide which content is worth keeping* — a much harder and quite different feature, closer to Feature 03. Please confirm which we are promising, because it is the difference between a tool we can ship in phase 1 and one we cannot.

---

### Feature 02 — Cinematic Colour Grading

*Returns decisions · fast · previewed instantly, baked at export*

Makes footage shot on a phone look like it was shot deliberately.

| | |
|---|---|
| **User does** | Selects a clip, picks a look — the PRD names Cinematic, VLOG and Cyberpunk — or lets the system choose. |
| **System does** | Examines the lighting, exposure and colour balance of the footage and returns the colour profile that suits it, with a strength value — tuned to what it found, rather than the same flat filter regardless of scene. |
| **Puts on the timeline** | A grade attached to the clip, applied live in the preview and baked in at export. |
| **User can then** | Change the look, dial the strength up or down, or remove it. Not edit it frame by frame. |

**Still to confirm**

1. Exactly which looks ship at launch? Three is a thin catalogue; competitors offer dozens.
2. Can users bring their own colour profile (a LUT file), or is the list fixed?
3. One grade for a whole clip, or a different grade per scene when the lighting changes?

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

**Still to confirm**

1. How many clips come out of one video, and does the user choose the number?
2. How long should a clip be — 15, 30, 60 seconds? Fixed or variable?
3. What is the longest recording we accept? A three-hour stream is a large and expensive piece of processing.
4. Do the clips arrive already captioned and graded, or does the user apply those afterwards?

🔴 **A whole capability is missing from both source documents.** Turning a landscape recording into a vertical clip means throwing away about two-thirds of the width of the picture. There are only two ways: crop the middle and accept that people sitting off-centre get cut in half, or **track the speaker and move the crop to follow them**, switching between speakers as they talk. Speaker tracking is what every competing product does, it is substantial work, and it appears nowhere. A centre-crop of a two-person podcast produces unusable clips. This is the main reason this tool is not proposed for phase 1.

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

**Still to confirm**

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
| **Puts on the timeline** | A new version of the clip's media, in place of the original. |
| **User can then** | Revert to the original. The original is never destroyed. |

**Still to confirm**

1. Is this one button, or four switches the user controls independently? **We propose four**, and we propose splitting them across phases — noise and echo removal are audio-only and comparatively cheap, while sharpening and stabilisation are the expensive per-frame work.
2. What resolution does sharpening aim for — 1080p, 4K?
3. Stabilisation normally zooms in slightly and loses the edges of the frame. Is that acceptable, and does the user need to be told?

🟠 **This is the most expensive feature in the product.** Sharpening video means processing every individual frame — around 18,000 of them in a ten-minute video. It is by far the heaviest use of our processing hardware and can cost more to run than everything else combined. Decide whether it is limited to short clips, reserved for a paid tier, or priced at a credit cost that reflects what it really costs us.

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

**Still to confirm**

1. "30+ languages" — which ones exactly? And does it mean transcribing the language spoken, translating into other languages, or both? These are different features.
2. Are captions burned permanently into the picture at export, or also delivered as a separate subtitle file the platform can switch on and off?
3. How many caption styles, and can the user control position, size and colour? Captions placed where TikTok puts its own interface get covered up.
4. In a two-person conversation, do we label who is speaking?

🔵 **This is the tool that proves the editor model is right.** Speech recognition always mis-hears names, brands and technical terms. Under a one-shot generator, a customer's name burned into the video in large animated letters, spelled wrong, is unfixable. In an editor, it is a two-second correction.

---

### Core differentiator — 3D Face Mapping with Lip Sync

*Returns pixels · slow · the product's signature feature · not in phase 1*

Places a face into video and keeps it stable in every frame, instead of the flickering and distortion other tools produce, with the mouth moving in time with the audio.

| | |
|---|---|
| **Set up, once** | The user uploads three photographs of a face — left profile, straight on, right profile. The system builds a three-dimensional model of it (shape, skin, how light falls across it) and saves it to their account as a reusable face profile. |
| **Each time they use it** | The user selects a clip and one of their saved face profiles. The system replaces the face of the person in that clip, frame by frame, following the head as it turns and keeping the original expressions, and adjusts the mouth to match the audio. |
| **Puts on the timeline** | A new version of the clip's media, in place of the original. |
| **User can then** | Revert to the original, or re-run with a different face profile. |

🟢 **Decided:** the target clip can be the user's own footage **or** any clip they import (§4.3). A consent checkbox is shown at upload.

🟢 **Decided:** lip sync ships with it (§4.5), scoped as re-syncing the mouth to the existing audio.

**Still to confirm**

1. **Whose face can be uploaded?** Only the account holder's own face, verified somehow? Or any three photographs of anyone? §4.3 settled whose *video* can be targeted, but not whose *face* can be supplied.
2. The PRD says "real-time mapping". The architecture document describes a queued job of about 45 seconds. We have assumed "real-time" was meant loosely, as "automatic". Please confirm we are not promising a live preview on the timeline — that would be a fundamentally different and far more expensive system.

---

## 8. Accounts, credits and limits

Each user has an account and a balance of credits. **Ordinary editing is free** — a user charged for splitting a clip will not use the product. Credits pay for the AI tools, and possibly for export.

| Area | Status |
|---|---|
| **Pricing** | 🟠 Open. What each tool costs in credits. Sharpening a 4K video and captioning a 30-second clip cannot cost the same. |
| **Buying credits** | 🟠 Open. Which payment provider, in what bundles. **There is no payment system anywhere in the source architecture.** |
| **Free tier** | 🟠 Open. Whether new users get free credits, how many, and what they can do with them. |
| **Subscriptions** | 🟠 Open. Whether there are monthly plans as well as credits. |
| **Failed jobs** | 🟢 Resolved by design. Credits are reserved when a job starts and only charged on success; a failure releases the reservation automatically. See [`03-backend-architecture.md`](03-backend-architecture.md) §5.4. |
| **Export** | 🟠 Open. Whether exporting costs credits, and whether free exports carry a watermark. |
| **Upload limits** | 🟠 Open. Maximum file size, video length, resolution, accepted formats. The architecture proposes defaults; they need confirming. |
| **Storage** | 🟠 Open. How long we keep a user's media and projects. A direct and recurring cost — we store whole projects, not just finished videos. |
| **Teams** | 🟠 Open. Whether an account is one person or a company sharing a balance. |
| **Sign-in** | 🟠 Open. Whether Google/Apple sign-in is offered, or only email and password. |

🟠 **Someone needs to own the commercial model.** It is not a backend question, but the backend cannot be finished without it. Pricing and payment are the only items on this list that block phase 1 shipping — everything else can be defaulted.

---

## 9. Faces, consent and generated video

The project lead's answer in §4.3 settles the *product* question: face mapping works on imported footage, with a consent checkbox. It does not settle the compliance work behind it, and that work is real.

**Agreed handling:** this is deferred to the face-mapping phase, not to launch. Phase 1 contains no facial data at all, which is precisely what makes the deferral safe. What follows is the list that has to be worked through before that phase starts — recorded now so it is not rediscovered late.

- **A consent step that records something useful** — who the face belongs to, that the user asserts the right to use it, the wording they accepted, and when. A checkbox with no record is worse than no checkbox, because it proves nothing.
- **Deletion that actually deletes** — removing a face profile and everything derived from it, including every project it was used in and every rendered output.
- **Marking generated video** — a visible watermark, invisible provenance data (C2PA) embedded in the file, or both. Also a business decision: a visible watermark on free output is a pricing lever as much as a compliance one.
- **A position on misuse** — what we block, how we detect it, how someone reports it, and what happens then.
- **Retention** — how long face data survives after a user stops using the product or closes their account.

🔴 **This needs a named owner before the face-mapping phase starts.** None of it can be decided by the development team, and none of it can be bolted on at the end — the answers change what we are allowed to store and for how long, which changes the database. We recommend a legal opinion covering the markets we intend to launch in, obtained while phase 1 is being built.

---

## 10. What the product does not do

Proposed **out of scope for the whole roadmap**, not just phase 1. If you disagree with any line, that is a scope change and we need to know now. (What is out of scope for *phase 1 specifically* is in [`02-scope-v1.md`](02-scope-v1.md).)

| Not building | Note |
|---|---|
| **Publishing to TikTok, YouTube or Instagram** | The PRD talks about "Reels and Shorts growth", which implies it — but publishing directly to those platforms is real work with each platform's approval process attached. 🟠 **Still needs an explicit yes or no.** |
| **Collaboration** | No sharing a project with a teammate, no comments, no review. |
| **Multi-camera editing** | One angle at a time. |
| **Keyframed animation** | No custom motion paths or property animation over time. |
| **Green screen / background removal** | Not mentioned anywhere, commonly expected in this category. Flagging it deliberately. |
| **A stock footage or b-roll library** | Users bring their own footage. |
| **AI voiceover or voice cloning** | The product cleans up the voice in the recording. It does not generate one. See §4.5 — this is the line lip sync must not cross. |
| **Script or content generation** | No idea generation, no writing, no titles or descriptions. |
| **Analytics on published videos** | We do not track how a video performed after it left us. |
| **Working offline** | The AI tools cannot work offline, and export is server-side. The app requires a connection. |

---

## 11. Assumptions we have made

Where the source documents were silent, we filled the gap. Each of these could be wrong, and several carry real work behind them.

1. The audience is individual content creators publishing short vertical video, plus podcasters and streamers.
2. Ordinary editing is free and runs in the browser; only the AI tools and export cost credits.
3. The original media is never destroyed — every AI result can be reverted.
4. "Real-time" in the PRD means "automatic, without manual work", not "live on screen as it happens".
5. AI tools can be combined freely on one project — a user can trim, grade and caption the same footage.
6. The interface is in English at launch. The "30+ languages" refers to speech in users' videos, not to the app's own interface.
7. Projects and media are private to the user by default; nothing is public or shared unless they publish it themselves.
8. Lip sync means re-syncing a mapped face to the existing audio, not making anyone say new words (§4.5).
9. The claimed figures in the PRD — 98% transcription accuracy, 80% retention improvement — are marketing statements, not targets the system will be measured against. If they are meant as commitments, we need to say so now: guaranteeing 98% accuracy across 30 languages is a very different undertaking.

---

## 12. Decision register

### Answered

| # | Decision | Answer |
|---|---|---|
| **1** | Editor with AI tools, or automatic generator? | 🟢 Editor, CapCut style. AI tools in the toolbar. |
| **2** | How do we fit the editor and the AI features into one release? | 🟢 Phased release. Clean basic editor + 2–3 core AI tools first, the rest incrementally. |
| **3** | Whose video can a face be mapped onto? | 🟢 Both own footage and imported clips, with a consent checkbox at upload. |
| **4** | Web, iOS, Android — which, and when? | 🟢 Web first. Mobile later, reusing the same backend. |
| **5** | Is lip sync in the product? | 🟢 Yes, alongside face mapping. Scoped as re-sync, not re-voice. |
| **9** | How does the user preview a server-side effect? | 🟢 Resolved by design — browser preview on proxy media, nothing round-trips to see an edit. |
| **10** | Where does export happen? | 🟢 Resolved by design — server-side, one render path. Whether it costs credits is still open. |

### Open

| # | Decision | Why it matters | When |
|---|---|---|---|
| **A** | **Which 2–3 AI tools ship in phase 1?** | Raised by the phased-release answer. Determines whether the backend needs a GPU inference cluster on day one or not. A proposal is in [`02-scope-v1.md`](02-scope-v1.md) — confirm or change it. | 🔴 **Blocks the backend build** |
| **B** | What does each tool cost in credits, and how are credits bought? | There is no payment system in the source architecture at all. Nothing can be charged for without this. | 🔴 **Blocks launch** |
| **C** | Does Smart Trimming tighten a recording, or cut it down to its best parts? | The stated 10-minutes-to-2 result is a different, harder feature. Changes whether it can ship in phase 1. | 🟠 Before phase 1 build |
| **D** | Whose face can be uploaded — only the account holder's, or anyone's? | §4.3 settled whose video can be targeted, not whose face can be supplied. | 🟠 Before face-mapping phase |
| **E** | Who owns the consent, watermarking and misuse work, and when is legal advice obtained? | Deferred by agreement, not cancelled. Needed before the face-mapping phase, not before launch. | 🟠 Before face-mapping phase |
| **F** | Does the Clip Finder track speakers when converting to vertical? | Without it, clips of more than one person are unusable. Substantial work, in neither document. | 🟠 Before that tool is built |
| **G** | Does export cost credits, and do free exports carry a watermark? | Commercial lever as well as a cost question. | 🟠 Before launch |
| **H** | Do templates include music, and who licenses it? | A commercial agreement with a cost attached, not a development task. | ⚪ Before that tool is built |
| **I** | Do we publish directly to TikTok, YouTube and Instagram? | Implied by the PRD but never specified. Each platform has its own approval process. | ⚪ Before launch |

---

## What happens next

With the position confirmed, the technical design proceeds:

| Document | For | Status |
|---|---|---|
| [`02-scope-v1.md`](02-scope-v1.md) | Everyone — read first | Proposes the phase 1 tool selection (open decision A) |
| [`03-backend-architecture.md`](03-backend-architecture.md) | Backend team | Data model, job pipeline, infrastructure |
| [`05-api-contract.md`](05-api-contract.md) | Both teams | The interface both sides build against |
| [`04-frontend-architecture.md`](04-frontend-architecture.md) | Frontend | Timeline state, playback engine, tool integration |

**The one thing needed before the backend team can start is decision A** — which tools ship in phase 1. Everything else in the architecture holds regardless of the answer.

---

*AI Video Editor · Product Vision v1.0 · 12 August 2026*
*Compiled from "Next-Gen AI Video Editor — Product Feature Specifications & Backend Requirements", "System Architecture & Backend Design" (draft), and the project lead's decisions of 12 August 2026.*
