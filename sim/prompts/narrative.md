SYSTEM
Write a single headline for a retail brand manager, under 20 words.
Use only numbers present in the input JSON. If a number is missing, do not mention it.
Report what the numbers say, including when they are unflattering. Do not add caveats, advice, or a second sentence.
Respond with only JSON: {"headline": "<the sentence>"}

USER
The JSON below is the whole report. Every number in it has already been computed; nothing else is known.
A null, or a field that is absent, means that quantity was not measured — say nothing about it.

Write the headline about whichever of these the numbers actually support, in this order of preference:
1. relative_agreement — how much of the real panel's own repeatability the synthetic panel reached.
   If you use it, also state noise_ceiling.spearman_mean: relative_agreement is capped at 1, so it
   means nothing without the ceiling it is a fraction of.
2. known_effect — whether both panels moved the focal SKU's attention the same way.
3. decision_agreement — whether both panels picked the same winning variant.
4. ad_to_purchase_lift — the advertised brand's purchase lift among ad-exposed shoppers.
If none of those were measured, say that the synthetic panel ran and the real panel has not been collected yet.

{results_json}
