# Museum Feature Plan - Detailed

## Context
The museum guide flow combines RAG retrieval, image extraction, and product recommendation
in a single SSE pipeline. Current behavior shows three classes of problems:
1) Image and product reuse across turns causes mismatch.
2) Context weighting is weak, so follow-up questions drift to irrelevant retrieval.
3) Product recommendation is tightly coupled to artifacts, leaving no path for
   non-artifact merchandise.

This document proposes a concrete plan to address each problem with clear changes,
deliverables, and acceptance criteria.

## Goals
- Keep images, text, and products aligned per turn.
- Improve follow-up continuity without polluting new topics.
- Allow non-artifact merchandise to be recommended and surfaced clearly in UI.
- Maintain backward compatibility and allow staged rollout.

## Non-Goals
- Rebuild the entire RAG stack.
- Redesign the full UI/UX for the guide page.
- Replace the current knowledge base vendor.

## Current Flow (Simplified)
1) User message -> GuideService.process_guide
2) RAG search -> image extraction -> product bridge lookup
3) SSE events: SEARCH_RESULT, SEARCH_IMAGES, PRODUCT_RECOMMEND, MESSAGE_CHUNK, MESSAGE_END
4) Frontend stores results in guideStore and renders MultimodalOutput

## Problem A: Mixed Architecture and Reuse -> Mismatch

### Symptoms
- If second turn has no image hit, previous turn images are reused.
- Images/products may refer to different artifacts than the current response.
- UI can display stale recommendations if no new event arrives.

### Root Cause Hypothesis
- Image fallback uses last cached images without topic gating.
- Product recommendation has no fallback event to clear the UI.
- No "turn_id/topic_id" binding for multimodal payloads.

### Plan
1) Introduce turn-level identity and topic gating:
   - Compute a "topic_id" per turn.
   - Cache images and products with topic_id and turn_id.
   - Reuse only when the new query is a follow-up to the same topic.
2) Add explicit "empty" events to clear images/products in UI.
3) Add "source" metadata to SSE payloads (new, cached, none).

### Backend Changes
- GuideService:
  - Compute turn_id for each request.
  - Add topic detection (follow-up vs new topic).
  - Cache images/products with topic_id.
  - If no valid images for this topic, emit SEARCH_IMAGES with empty list and source=none.
  - If no products for this topic, emit PRODUCT_RECOMMEND with empty list and source=none.
- MessageHistory (or new cache helper):
  - Store images/products per topic_id.

### Frontend Changes
- useGuideSSE:
  - On SEARCH_IMAGES with empty list, clear image UI.
  - On PRODUCT_RECOMMEND with empty list, clear product UI.
  - Track topic_id in store to avoid stale rendering.

### Acceptance Criteria
- No cross-topic image reuse.
- If no images/products are found for a new query, UI clears the previous ones.

## Problem B: Weak Context Engineering -> Retrieval Drift

### Symptoms
- Follow-up questions retrieve irrelevant content.
- Query rewrite does not consider conversation history.
- Current logic only injects last user keywords, which is fragile.

### Root Cause Hypothesis
- DocSkill is called without messages/history.
- Query rewriter history support is not used.
- No topic continuity classifier, so history is injected inconsistently.

### Plan
1) Pass recent conversation history to DocSkill and QueryRewriter.
2) Add a lightweight "follow-up vs new topic" classifier.
3) Use a weighted query builder:
   - Follow-up: include prior topic summary + current question.
   - New topic: exclude previous topic and use current question only.
4) Store topic summary per turn to improve continuity.

### Backend Changes
- GuideService:
  - Fetch recent history messages and pass to DocSkill.
  - Add topic classifier (rules or embedding similarity).
  - Build enriched query based on classifier output.
- DocSkill:
  - Accept and use messages in query_rewriter.rewrite.
  - Keep existing defaults for backward compatibility.

### Acceptance Criteria
- Follow-up queries stay on-topic in manual test set.
- New topic queries do not "drag" old topic keywords.

## Problem C: Product Recommendation Too Strict

### Symptoms
- Products only appear when linked to artifact IDs.
- No way to show curated, themed, or popular merchandise.

### Root Cause Hypothesis
- Product lookup is tied to artifact bridge lookup only.
- Data model lacks "curated/theme/tags" fields.

### Plan
1) Add a non-artifact recommendation pool:
   - Curated picks
   - Themed picks (by tag/keyword)
   - Popular items (by sales/engagement)
2) Provide a clear ranking chain:
   - Related (artifact-based) -> Curated -> Themed/Keyword -> Popular
3) Mark recommendation_type in SSE payload for UI display.
4) Define how the LLM sees these items and when it should mention them.

### Backend Changes
- Database:
  - Add optional fields: tags (json), is_featured (bool), theme (string),
    popularity_score (float), source (string).
- ProductService:
  - New queries: get_curated_products, get_theme_products, get_popular_products,
    search_products_by_keyword.
- GuideService:
  - When no related products, fall back to curated/theme/popular chain.
  - Emit recommendation_type in PRODUCT_RECOMMEND payload.

### Frontend Changes
- MultimodalOutput:
  - Show product section labels: "Related" vs "Museum Picks" vs "Trending".
  - Optional pill tag for recommendation_type.

### LLM Exposure and Guidance (How the LLM Gets and Mentions These Items)
- The LLM should not query the database directly. The server composes a small,
  curated list and injects it into the generation context.
- GuideService should pass a structured "product candidates" block to the prompt:
  - Include only minimal fields: name, price, short reason, source, tags (optional).
  - Limit to top 2-3 items to avoid prompt bloat.
- Add a simple "shopping intent" signal (rule-based or classifier):
  - If the user asks about buying, gifts, or souvenirs -> allow the LLM to mention
    1-2 items by name.
  - Otherwise, the LLM should only use a soft cue like "see the picks below".
- Prevent hallucination by prompt rule:
  - "Only mention products that appear in the product candidates list."
  - "Do not invent prices or availability."

Example context block (server-side):
```
## Product candidates (source: curated)
- name: Bronze Pattern Notebook, price: 38, reason: theme=patterns
- name: Jade Motif Bookmark, price: 25, reason: gift-friendly
```

Example prompt instruction:
- If shopping_intent=true, mention at most two items from the list.
- If shopping_intent=false, do not name items, just point to the product area.

### Acceptance Criteria
- At least one non-artifact recommendation path is visible.
- UI can clearly label the product source.
- LLM mentions products only when intent suggests it, and only from provided list.

## API and SSE Changes
- SEARCH_IMAGES payload:
  - { images: [...], source: "new|cached|none", topic_id: "..." }
- PRODUCT_RECOMMEND payload:
  - { products: [...], source: "related|curated|theme|popular|none", topic_id: "..." }

## Configuration and Rollout
- Feature flags:
  - MUSEUM_REUSE_IMAGES=false
  - MUSEUM_TOPIC_GATING=true
  - MUSEUM_CURATED_PRODUCTS=true
- Allow staged rollout and quick rollback.

## Testing Plan
- Unit tests:
  - Topic classifier (follow-up vs new topic)
  - Product fallback chain order
- Integration tests:
  - SSE payload correctness for empty results
  - UI clears stale results on empty events
- Manual tests:
  - Multi-turn conversation with topic switch
  - Follow-up question within same topic
  - No-hit retrieval with UI clear

## Observability
- Log fields: session_id, turn_id, topic_id, enriched_query, image_source, product_source.
- Metrics: image_hit_rate, product_hit_rate, reuse_rate, drift_rate.

## Timeline (Example)
- Week 1: Instrumentation and Problem A fixes
- Week 2: Context engineering and topic gating
- Week 3: Product recommendation pool and UI labels
- Week 4: Testing, rollout, and monitoring

## Risks and Mitigations
- Risk: Topic classifier is too strict -> low reuse rate.
  Mitigation: configurable threshold + metrics.
- Risk: Extra fields increase payload size.
  Mitigation: keep only essential metadata.
- Risk: Curated pool becomes stale.
  Mitigation: admin refresh or periodic update.

## Open Questions
- Follow-up classifier should be rule-based, embedding-based, or hybrid?
- Priority between curated vs themed recommendations?
- Required business KPIs for success?
