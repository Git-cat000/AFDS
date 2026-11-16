import { createServer } from "node:http";
import { expect, test as base } from "@playwright/test";

export const test = base.extend<{
  transcriptionContract: { requests: Buffer[] };
}>({
  transcriptionContract: async ({ request }, provideFixture) => {
    const requests: Buffer[] = [];
    const server = createServer((incoming, response) => {
      const chunks: Buffer[] = [];
      incoming.on("data", (chunk) => chunks.push(chunk));
      incoming.on("end", () => {
        requests.push(Buffer.concat(chunks));
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end(
          JSON.stringify({
            language: "en",
            segments: [
              { start: 0.25, end: 1, text: "DEMO contract first" },
              { start: 1.25, end: 2, text: "DEMO contract second" },
            ],
          }),
        );
      });
    });
    await new Promise<void>((resolve) =>
      server.listen(0, "127.0.0.1", resolve),
    );
    try {
      const address = server.address();
      if (!address || typeof address === "string")
        throw new Error("Missing contract address");
      const profiles = await (
        await request.get("/api/v1/settings/models")
      ).json();
      const previous = profiles.providers.find(
        (item: { kind: string }) => item.kind === "transcription",
      );
      const saved = await request.put(
        "/api/v1/settings/providers/transcription",
        {
          data: {
            revision: previous?.revision || 0,
            base_url: `http://127.0.0.1:${address.port}/v1`,
            model: "DEMO-transcription",
            api_key: "test-only-credential",
            enabled: true,
            allow_private_network: true,
            transcription_timestamps: true,
          },
        },
      );
      expect(saved.status()).toBe(200);
      await provideFixture({ requests });
    } finally {
      server.closeAllConnections();
      await new Promise<void>((resolve, reject) =>
        server.close((error) => (error ? reject(error) : resolve())),
      );
    }
  },
});

export function demoClickTrack() {
  const wav = Buffer.alloc(44 + 16000 * 4 * 2);
  wav.write("RIFF", 0);
  wav.writeUInt32LE(wav.length - 8, 4);
  wav.write("WAVEfmt ", 8);
  wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(16000, 24);
  wav.writeUInt32LE(32000, 28);
  wav.writeUInt16LE(2, 32);
  wav.writeUInt16LE(16, 34);
  wav.write("data", 36);
  wav.writeUInt32LE(wav.length - 44, 40);
  for (let start = 0; start < 64000; start += 8000) {
    for (let sample = start; sample < start + 100; sample++)
      wav.writeInt16LE(24000, 44 + sample * 2);
  }
  return wav;
}
