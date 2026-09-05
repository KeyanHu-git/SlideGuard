// Deterministic application resource: four crop corners, no third-party artwork.
import { mkdirSync, writeFileSync } from "node:fs";
const size = 32,
  pixels = size * size * 4,
  mask = size * 4,
  bitmap = 40 + pixels + mask;
const ico = Buffer.alloc(22 + bitmap);
ico.writeUInt16LE(1, 2);
ico.writeUInt16LE(1, 4);
ico[6] = size;
ico[7] = size;
ico.writeUInt16LE(1, 10);
ico.writeUInt16LE(32, 12);
ico.writeUInt32LE(bitmap, 14);
ico.writeUInt32LE(22, 18);
ico.writeUInt32LE(40, 22);
ico.writeInt32LE(size, 26);
ico.writeInt32LE(size * 2, 30);
ico.writeUInt16LE(1, 34);
ico.writeUInt16LE(32, 36);
ico.writeUInt32LE(pixels, 42);
for (let y = 0; y < size; y++)
  for (let x = 0; x < size; x++) {
    const corner =
      (((x >= 7 && x <= 9) || (x >= 22 && x <= 24)) &&
        ((y >= 7 && y <= 13) || (y >= 18 && y <= 24))) ||
      (((y >= 7 && y <= 9) || (y >= 22 && y <= 24)) &&
        ((x >= 7 && x <= 13) || (x >= 18 && x <= 24)));
    const [r, g, b] = corner ? [181, 199, 246] : [32, 34, 38],
      i = 62 + ((size - 1 - y) * size + x) * 4;
    ico[i] = b;
    ico[i + 1] = g;
    ico[i + 2] = r;
    ico[i + 3] = 255;
  }
mkdirSync("src-tauri/icons", { recursive: true });
writeFileSync("src-tauri/icons/icon.ico", ico);
