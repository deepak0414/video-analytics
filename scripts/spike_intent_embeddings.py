"""SPIKE: can SigLIP's text encoder separate question INTENTS by exemplar cosine?

Method: embed a few exemplar questions per intent; embed held-out probes (real
phrasings from our incident history + never-seen ones); intent score = max cosine
to that intent's exemplars. Pass = correct intent wins per probe, with a usable
margin, and a clean binary threshold for the deep_scan escalation decision.

Run: VA_CONFIG_DIR=run-siglip/config .venv/bin/python scripts/spike_intent_embeddings.py
"""
import numpy as np

from va.registry import get_visual_embedder

EXEMPLARS = {
    "deep_scan": [
        "how many times does something happen in the video?",
        "count the occurrences of an event over time",
        "how often does the subject do something?",
        "count how many times the state changes",
    ],
    "transcript": [
        "what did they say about the topic?",
        "when did someone mention something?",
        "what was spoken about in the video?",
    ],
    "visual": [
        "what color is the object?",
        "what does the scene look like?",
        "who is wearing something visible?",
    ],
    "object_count": [
        "how many distinct objects appear in the video?",
        "how many different things are visible overall?",
    ],
}

# held-out probes: (question, expected_intent) — none of these are exemplars
PROBES = [
    # our actual incident-history phrasings
    ("the girl in first scene, how many time she changes her dress in the entire video clip?", "deep_scan"),
    ("count number of birds visiting birdfeeder in the clip", "deep_scan"),
    ("How many birds come and feed on the feeder?", "deep_scan"),
    ("Girl in the first scene, how many dresses she changes in the clip?", "deep_scan"),
    # never-seen phrasings (the generalization test)
    ("how often do birds drop by?", "deep_scan"),
    ("how many times does the traffic light switch?", "deep_scan"),
    # negatives
    ("what color is the t-shirt of the person entering the red car?", "visual"),
    ("what did the woman say about the dresses?", "transcript"),
    ("when do they talk about the budget?", "transcript"),
    ("how many distinct cars appear?", "object_count"),
    ("find the kitchen scene", "visual"),
]


def main() -> None:
    emb = get_visual_embedder()
    ex_vecs = {k: emb.embed_text(v) for k, v in EXEMPLARS.items()}

    correct = 0
    ds_pos, ds_neg = [], []   # deep_scan binary-threshold analysis
    print(f"{'probe':<68} {'predicted':<13} {'expect':<13} margin")
    for question, expected in PROBES:
        q = emb.embed_text([question])[0]
        scores = {k: float(np.max(vecs @ q)) for k, vecs in ex_vecs.items()}
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        predicted, top = ranked[0]
        margin = top - ranked[1][1]
        ok = predicted == expected
        correct += ok
        (ds_pos if expected == "deep_scan" else ds_neg).append(scores["deep_scan"])
        flag = "" if ok else "  <-- WRONG"
        print(f"{question[:66]:<68} {predicted:<13} {expected:<13} {margin:+.3f}{flag}")

    print(f"\naccuracy: {correct}/{len(PROBES)}")
    print(f"deep_scan score range  — positives: {min(ds_pos):.3f}..{max(ds_pos):.3f}"
          f"   negatives: {min(ds_neg):.3f}..{max(ds_neg):.3f}")
    gap = min(ds_pos) - max(ds_neg)
    print(f"binary separability gap: {gap:+.3f} "
          f"({'CLEAN threshold exists' if gap > 0 else 'OVERLAP — SigLIP text tower insufficient, try a sentence encoder'})")


if __name__ == "__main__":
    main()
