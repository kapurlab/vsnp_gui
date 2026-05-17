# R1 — User friction log: Lingling (grad student, MTBC panel)

**Persona:** Graduate student, veterinary microbiology, non-CS background.  
**Task from advisor:** Check whether 16 MTBC samples carry beta-lactam resistance genes using the new pipelines GUI.  
**Prior vsnp_gui experience:** Two sessions (step1 + step2 on MTBC). Zero experience with AMRFinder, kraken, sourmash.  
**Time budget:** 20 minutes before advisor's meeting.

---

## Friction log

```
T+0:00  I open the OOD dashboard. There's the vSNP card I know. But now
        there are more cards — something called Kraken, something called AMR,
        one called Sourmash. I've never heard of most of these. My project
        from last week — the 16 MTBC samples — I can see it listed... somewhere?
        Wait. Is my project *in* the vSNP card or in the AMR card? Do I click
        vSNP first and then somehow get to AMR from there?

        [Confusion: I don't know if these cards share a project or if they're
        separate applications. The doc says "user picks a project once, every
        card sees it" — but I never read the doc. I just see four launch buttons.]

T+0:45  I click the AMR card because my advisor said "AMR." It opens.
        I see... something. The doc says it's a card with a project selector
        and a sample list. But what does the project selector actually look like?
        Is it a dropdown? Do I type a path? My project is at
        /home/vxk1/projects/MTBC_16/ or something — I don't remember the name
        I gave it.

        [Doc-gap: I cannot tell from the doc what the project selector looks like
        in the AMR card UI. It mentions "user picks a project once" and a
        ?project=X URL parameter but nothing about what I see when I first open
        the card cold with no URL parameter.]

T+1:30  I pick what I think is my project from a list (assuming there's a list).
        16 samples appear. Good. I recognize the sample names — these are mine.

        [Surprise (good): The samples are all there from last week. That's reassuring.]

T+2:00  I look at the sample rows. The doc mentions "badge column on the sample
        row." Each row probably shows a colored chip. For all 16 samples the AMR
        badge column is blank or grayed out — AMR hasn't run yet.

        [Doc-gap: What does "not yet run" look like? Is it a gray chip labeled
        "Not run"? Is the column just empty? Is there a spinner? I'm imagining
        a gray dash. If it's just blank I might think it broke.]

T+2:30  There should be a button somewhere to run AMR. The doc says Step 2 of
        the migration path adds a "Run AMR" button on step1 result pages. But
        I'm in the AMR card, not the vSNP card. Is there a "Run AMR" button here?
        Or do I have to go back to vSNP first?

        [Confusion: The doc describes the "Run AMR" button appearing in vsnp_gui
        on step1 result pages. But if I opened the AMR card directly from the
        dashboard, does it have its own "Run" button? The doc doesn't describe
        what the AMR card's own UI looks like at all — only what vsnp_gui's step1
        page exposes. This is a real gap.]

T+3:15  I try clicking on one sample row to see if something happens. Nothing,
        or a detail panel opens. No obvious "Run" button.

        [Dead-end: I don't know how to start the AMR run from this card.]

T+4:00  I go back to the vSNP card. I remember I ran step1 and step2 last week.
        I find my project, I find the sample list. There's a badge column — maybe.
        The doc says badges appear for completed primitives. My step1 is complete
        so there should be step1 badges. There might be an AMR badge column that
        says "Not run."

        [Hesitation: Do I click the badge or somewhere else? Is there a "Run AMR"
        button per sample or one "Run AMR on all 16" button? My advisor said 16
        samples — I do not want to click 16 times.]

T+5:00  I find what I think is a "Run AMR" button. Maybe it's per-sample, maybe
        it's a bulk action. I click it.

        [Doc-gap: The doc sketches a per-sample button (the ?project=X&sample=Y
        URL pattern implies one sample at a time). There is no mention of a bulk
        "Run AMR on all samples" action. If I have to click 16 times — once per
        sample — I will notice this immediately and it will feel broken.]

T+5:15  A new tab or panel opens — maybe the AMR card with ?project=MTBC&sample=Sample01
        pre-filled. Or maybe a modal. I don't know which.

        [Doc-gap: "Opens the AMR OOD card with ?project=X&sample=Y pre-filled" —
        does this open a new browser tab? A new OOD session? A modal inside the
        current session? If it opens a whole new OOD session I have to wait for
        it to spin up. That's 30-60 seconds per sample. Times 16. I will not do
        this 16 times.]

T+6:00  Assuming it's a modal or inline panel: I see the AMR run is starting for
        Sample01. There's presumably some kind of progress indicator. The doc says
        AMRFinder takes ~5 seconds per genome. So maybe 5 seconds later it's done.

        [Surprise (good): 5 seconds is fast. If there's a spinner that completes
        that quickly I'll feel like it worked.]

T+6:30  A badge appears: "AMR: 0 genes" or "AMR: none" with a green chip. Wait —
        this is MTBC. The doc's badge logic is calibrated for Mammaliicoccus sciuri
        (mecA1, MRSA-like). Will it make sense for Mycobacterium tuberculosis
        complex? The badge says "AMR: none" with verdict=pass. My advisor asked
        about beta-lactam resistance specifically.

        [Confusion: Does "AMR: none" mean no beta-lactam genes, or no AMR genes
        at all? These are different things. If there are AMR genes in a different
        class but no beta-lactams, the badge might say "AMR: 2 gene(s)" verdict=review
        with no mention of beta-lactam specifically. I would not know from the badge
        alone whether my advisor's question is answered.]

T+7:30  I have to do this for all 16 samples. If it's per-sample clicks: I click
        sample 2. Repeat. Repeat. By sample 5 I'm looking at my watch.

        [Hesitation / Dead-end: No bulk run path is described anywhere in the doc.
        For 16 samples this interaction pattern will take the full 20 minutes just
        on clicking, before I can read any results.]

T+12:00 Assuming I've clicked through all 16 (or found a bulk action by accident):
        Now I have 16 badges on the sample list. Some say "AMR: none," some might
        say "AMR: 2 gene(s)." I need to know specifically about beta-lactam.

        [Doc-gap: The sample list view with badges — can I filter by AMR class?
        Can I see a matrix (like the amr_matrix.csv we produced manually) in the
        GUI? The doc says .web() returns findings with class_colors and the badge
        renders verdict, but nowhere does it describe a cross-sample matrix view
        inside any GUI. The matrix existed as a manual CSV. Without it I have to
        click into each sample's detail to check whether the AMR findings include
        BETA-LACTAM class.]

T+14:00 I open sample detail panels one by one. Each shows gene names I've never
        seen: blaTEM, blaOXA, etc. — or nothing. I'm not sure if "beta-lactam"
        is labeled as "BETA-LACTAM" in the table or something else. The doc shows
        CLASS_COLORS with "BETA-LACTAM" in red (#d1495b). If the UI uses that
        color I'll recognize it. But if I'm reading gene names I won't know which
        ones are beta-lactams without Googling.

        [Confusion: Organism mismatch compounds this. AMRFinder was smoke-tested
        on Mammaliicoccus sciuri. MTBC has a different resistance landscape.
        I don't know if running AMRFinder on MTBC assemblies even makes biological
        sense, and the GUI gives me no guidance about this. The doc says
        .applicable() checks for an assembly FASTA — it does NOT check whether
        the organism has a meaningful AMR profile.]

T+16:00 I have not found a clear yes/no answer about beta-lactam resistance across
        my 16 samples. I have a list of badges and some gene names. I don't have
        a summary table. I don't know if I should trust the results on MTBC.

T+18:30 I send my advisor a Slack message: "I think I ran it, but I'm not sure
        the results are right for TB samples. Can you check?"

T+20:00 Meeting starts. Task: incomplete.
```

---

## Did I complete the task?

No. I was not able to confidently answer whether my 16 MTBC samples carry beta-lactam resistance genes within 20 minutes. Two blockers:

1. No bulk run — the per-sample interaction model was not described for batch use, and 16 samples would require 16 separate triggering actions.
2. No cross-sample matrix in the GUI — I got per-sample badges but no consolidated view that let me answer "which of the 16 samples have BETA-LACTAM class genes?"

A third softer blocker: organism validity. The system would have run AMRFinder on MTBC without warning me that the tool's smoke-testing and badge logic were calibrated for a different species. I would not know whether to trust the output.

---

## Reflection

**The single biggest friction point:** No bulk run action. The design describes the "Run AMR" trigger as a per-sample button that opens a card with `?project=X&sample=Y`. For a 16-sample panel, this is not a workflow — it is a chore. The design doc is written from the perspective of the infrastructure (one route handler per card, trivial to implement), but the user lives in the aggregate. A "Run AMR on all samples in project" action needed to exist before Step 2 shipped.

**Whether the design's mental model matches a grad student's expectation:** Mostly no. The design's mental model is "cards are lenses over a shared project tree." A grad student's mental model is "I pick a project, I press a button, I get a table." The card metaphor requires the user to understand that AMR is a separate card from vSNP but shares the same project data — this is an infrastructure concept, not a workflow concept. A user who opens the AMR card directly from the OOD dashboard and sees their project data already there will be pleased. A user who doesn't know which card to start in will be lost.

**One thing the design got right:** The `samples.json` shared knowledge base. If the badges light up automatically after a run without requiring me to navigate anywhere, and if they're visible on the vSNP sample list I already know, that is genuinely good. I already have a mental model of "my project" from the vSNP sessions — surfacing AMR findings inside that familiar context is the right move.

**One thing that would have been invisible to the designer but felt obvious in role:** The gap between "a badge appears" and "I can answer my advisor's question." The badge says `AMR: 2 gene(s), verdict=review`. My advisor asked about beta-lactam specifically. The badge does not mention class. The detail tooltip does (the `Badge.detail` field lists gene names), but gene names are not drug classes — a grad student who doesn't know that `blaTEM` is a beta-lactamase cannot close this gap from the badge alone. The designer, knowing the CLASS_COLORS map and the AMR gene nomenclature, would assume this is obvious. It is not.
