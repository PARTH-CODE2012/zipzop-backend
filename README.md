# ZipZop ad, v2

**[zipzop-ad-9x16.mp4](zipzop-ad-9x16.mp4)**: 52 seconds, vertical 9:16 (1080×1920), H.264 + AAC, with music. To watch it, open the file and click **View raw**.

This branch only holds the video. It shares no history with the code, so nothing here ever reaches `dev` or `main`. Delete the branch once the ad is final.

## Changed in v2 (8 October)

- **End screen:** it now says "Start with 300 free credits", with "Beta from ₹199/month" under it. New accounts really get 300 credits on the Free plan.
- **Hindi captions:** the captions scene now runs the AI a second time, on a clip spoken in Hindi, with the language set to **हिन्दी**. The words land on the caption track in Devanagari (चलो इसे सुन्दर बनाते हैं), show in the preview, and are burned into the exported video at the end.

## What's in it

| Time | Scene |
|---|---|
| 0–4 s | Hook: the raw clip wipes into the finished one (captions and grade) |
| 4–8 s | Logo: the wordmark inside the app's neon frame, then the tagline |
| 8–18 s | Step 1: upload clips and add them to the timeline |
| 18–28 s | Step 2: AI captions, in English, then in Hindi (Devanagari) |
| 28–38 s | Step 3: colour, browsing the five looks, then setting strength to 90 % |
| 38–44 s | Step 4: export, 4K vertical |
| 44–52 s | The exported file itself, with its Hindi line, then the end screen |

Everything on screen is the real app, run locally: real uploads, captions from the real Whisper job, the real colour grade, and the real exported file.

## Licences

- **Music:** made from scratch for this ad, with no samples or loops from anyone else. Free to use, nothing to pay.
- **Footage:** Pexels clips. The Pexels licence allows commercial use without credit.
- **Voices:** the voices only feed the captions and are not heard in the ad. The English one is a Windows voice; the Hindi one is a Microsoft neural voice.

## Placeholders to confirm

- **Logo:** the UI charter's wordmark ("zip" white, "zop" #FFE81F). We don't have an official logo yet.
- **"Up to 4K":** true on the Business plan only.
- **Hindi captions need the `small` Whisper model.** The default `base` model writes Hindi speech in Urdu script. The ad was made with `small`, so production has to use it too.
