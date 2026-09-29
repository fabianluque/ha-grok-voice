export interface HassLike {
  callWS(message: unknown): Promise<IngressInfo>;
  auth?: { data?: { access_token?: string } };
  connection?: { options?: { auth?: { accessToken?: string } } };
}

interface IngressInfo {
  data?: { ingress_entry?: string };
  ingress_entry?: string;
}

export function accessToken(hass: HassLike): string {
  return hass.auth?.data?.access_token || hass.connection?.options?.auth?.accessToken || "";
}

export function voiceSocketUrl(pageProtocol: string, host: string, ingressEntry: string): string {
  const proto = pageProtocol === "https:" ? "wss:" : "ws:";
  const path = ingressEntry.endsWith("/") ? ingressEntry : `${ingressEntry}/`;
  return `${proto}//${host}${path}`;
}

export async function resolveVoiceSocketUrl(hass: HassLike, pageProtocol: string, host: string): Promise<string> {
  const info = await hass.callWS({
    type: "supervisor/api",
    endpoint: "/addons/grok_voice_agent/info",
    method: "get",
  });
  const entry = info.data?.ingress_entry || info.ingress_entry;
  if (!entry) {
    throw new Error("Grok Voice ingress is not available for this user");
  }
  return voiceSocketUrl(pageProtocol, host, entry);
}
