// Fingerprint of a file: its size and the SHA-256 of its first and its last MiB (same as the sidecar's).
const MIB = 1024 * 1024;

async function sha256(blob) {
  const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer());
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export async function fingerprint(file) {
  return {
    size: file.size,
    head_sha256: await sha256(file.slice(0, MIB)),
    tail_sha256: await sha256(file.slice(Math.max(0, file.size - MIB), file.size)),
  };
}

export const sameFingerprint = (a, b) => a.size === b.size && a.head_sha256 === b.head_sha256 && a.tail_sha256 === b.tail_sha256;
