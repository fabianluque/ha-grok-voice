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

const SUFFIX_CLOSERS = new Set([
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
  "thatll do",
  "that will do",
  "thatll do it",
  "that will do it",
  "thats all thanks",
  "thats it thanks",
  "thanks thats all",
  "thanks thats it",
  "thank you thats all",
  "thank you thats it",
  "thats all for now",
  "that is all for now",
  "goodbye",
  "good bye",
  "bye",
  "bye bye",
  "good night",
  "goodnight",
  "thanks im done",
  "thank you im done",
  "im done thanks",
  "im all set",
  "were good",
  "we are good",
  "were all set",
  "we are all set",
]);

const EXACT_CLOSERS = new Set([
  "im done",
  "i am done",
  "were done",
  "we are done",
  "all set",
]);

const CLOSERS = new Set([...SUFFIX_CLOSERS, ...EXACT_CLOSERS]);

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
 * ("thank you for turning on the lights") is not a hang-up. Short phrases
 * like "I'm done" are exact-only so "tell me when I'm done" stays a request.
 */
const FOLLOWUP_PHRASES = [
  "do you want",
  "would you like",
  "want me to",
  "anything else",
  "need anything",
  "shall i",
  "should i",
];

/**
 * Assistant turns that are still waiting for an answer. Hang-up must not
 * fire while Grok asked a follow-up (question mark or ``Want …?``).
 */
export function isOpenFollowup(text: string | undefined): boolean {
  if (!text || !text.trim()) {
    return false;
  }
  const raw = text.trim();
  if (raw.includes("?")) {
    return true;
  }
  if (/(?:^|[.!]+\s+)want\b/i.test(raw)) {
    return true;
  }
  const normalized = normalizeUtterance(raw);
  return FOLLOWUP_PHRASES.some((phrase) => normalized.includes(phrase));
}

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
  for (const closer of SUFFIX_CLOSERS) {
    if (normalized.endsWith(` ${closer}`)) {
      return true;
    }
  }
  return false;
}
