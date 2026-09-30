const FILLERS = new Set([
  "ok",
  "okay",
  "alright",
  "please",
  "hey",
  "yeah",
  "yep",
  "yup",
  "grok",
  "uh",
  "um",
  "oh",
]);

const CLOSERS = new Set([
  "thank you",
  "thanks",
  "thank you so much",
  "thanks a lot",
  "thanks so much",
  "thank you very much",
  "thanks very much",
  "thats all",
  "thats it",
  "that is all",
  "that is it",
  "thatll be all",
  "that will be all",
  "thats everything",
  "that is everything",
  "thats all thanks",
  "thats it thanks",
  "thanks thats all",
  "thanks thats it",
  "thank you thats all",
  "thank you thats it",
  "goodbye",
  "good bye",
  "bye",
  "bye bye",
  "stop listening",
  "please stop listening",
]);

export function normalizeUtterance(text: string): string {
  return text
    .normalize("NFKC")
    .replace(/[\u2018\u2019`]/g, "'")
    .toLowerCase()
    .replace(/'/g, "")
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .join(" ");
}

export function stripFillers(text: string): string {
  const words = text.split(" ").filter(Boolean);
  while (words.length && FILLERS.has(words[0] ?? "")) {
    words.shift();
  }
  while (words.length && FILLERS.has(words[words.length - 1] ?? "")) {
    words.pop();
  }
  return words.filter((word) => word !== "grok").join(" ");
}

/**
 * Completed user turns that mean the conversation is over.
 *
 * Match the whole utterance, or a closer at the end after normalize
 * ("oh, that's great, thank you"). A closer in the middle of a request
 * ("thank you for turning on the lights") is not a hang-up.
 */
export function isClosingUtterance(text: string | undefined): boolean {
  if (!text || !text.trim()) {
    return false;
  }
  const normalized = stripFillers(normalizeUtterance(text));
  if (!normalized) {
    return false;
  }
  if (CLOSERS.has(normalized)) {
    return true;
  }
  for (const closer of CLOSERS) {
    if (normalized.endsWith(` ${closer}`)) {
      return true;
    }
  }
  return false;
}
