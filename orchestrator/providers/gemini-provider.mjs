export async function extractSpec() {
  throw new Error(
    "Gemini provider is not connected yet. Add GEMINI_API_KEY and implement this adapter behind the same extractSpec/reviewRender interface."
  );
}

export async function reviewRender() {
  throw new Error(
    "Gemini provider is not connected yet. The render-review contract is already isolated for this adapter."
  );
}
