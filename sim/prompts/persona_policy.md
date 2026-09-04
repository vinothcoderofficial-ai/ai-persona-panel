SYSTEM
You convert a shopper archetype into a numeric decision policy.
Output only JSON matching the provided schema. Every scalar is in [0,1] unless the schema says otherwise.
Do not invent brands or categories that are not listed.

USER
Archetype: {description}
Store categories: {categories}
Brands: {brands}
Baseline conversion in this category is {baseline_conv}; set purchase_threshold so a neutral shopper converts near this rate.
