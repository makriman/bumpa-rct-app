import { RequestError, type JsonObject } from "./security";

const IMAGE_MODEL = "@cf/black-forest-labs/flux-1-schnell";

export async function generateImage(ai: Ai, body: JsonObject): Promise<JsonObject> {
  const value = body.prompt;
  if (
    typeof value !== "string" ||
    value.length > 2_048 ||
    value.includes("\0") ||
    value.trim().length < 3
  ) {
    throw new RequestError("invalid_prompt", 400);
  }
  const response = await ai.run(IMAGE_MODEL, {
    prompt: value.trim(),
    steps: 4,
  });
  const image =
    response && typeof response === "object" && "image" in response
      ? response.image
      : null;
  if (typeof image !== "string" || image.length === 0 || image.length > 12_000_000) {
    throw new RequestError("invalid_image_response", 502);
  }
  return {
    image_base64: image,
    mime_type: "image/png",
    model: IMAGE_MODEL,
    untrusted_content: true,
  };
}
