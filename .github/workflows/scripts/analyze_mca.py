
import argparse
import csv
import gzip
import math
import os
import struct
import zlib
from collections import Counter
from io import BytesIO

import nbtlib


def decode_chunk(data):
    length = struct.unpack(">I", data[:4])[0]
    compression = data[4]
    payload = data[5:4 + length]

    if compression == 1:
        payload = gzip.decompress(payload)
    elif compression == 2:
        payload = zlib.decompress(payload)
    elif compression == 3:
        pass
    else:
        raise ValueError(f"Unsupported compression: {compression}")

    return nbtlib.File.parse(BytesIO(payload))


def get_value(tag):
    return int(tag)


def unpack_states(data, bits, count=4096):
    values = []
    mask = (1 << bits) - 1
    per_long = 64 // bits

    for i in range(count):
        word_index = i // per_long
        bit_offset = (i % per_long) * bits
        word = int(data[word_index]) & ((1 << 64) - 1)
        values.append((word >> bit_offset) & mask)

    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    region_name = os.path.basename(args.input)
    rx, rz = map(int, region_name.replace(".mca", "").split(".")[1:])

    chunk_rows = []
    block_rows = []
    total_blocks = Counter()

    with open(args.input, "rb") as f:
        header = f.read(8192)

        for slot in range(1024):
            location = header[slot * 4:slot * 4 + 4]
            offset = int.from_bytes(location[:3], "big")
            sectors = location[3]

            if offset == 0 or sectors == 0:
                continue

            f.seek(offset * 4096)
            chunk_data = f.read(sectors * 4096)

            try:
                chunk = decode_chunk(chunk_data)
                root = chunk.get("")

                cx = rx * 32 + (slot % 32)
                cz = rz * 32 + (slot // 32)

                if "Level" in root:
                    level = root["Level"]
                else:
                    level = root

                sections = level.get("sections", level.get("Sections", []))
                chunk_counts = Counter()
                chunk_positions = []

                for section in sections:
                    section_y = get_value(section.get("Y", 0))

                    states = section.get("block_states", section.get("BlockStates"))
                    if states is None:
                        continue

                    palette = states.get("palette", states.get("Palette"))
                    data = states.get("data", states.get("BlockStates"))

                    if not palette:
                        continue

                    names = [
                        str(entry.get("Name", entry.get("name", "minecraft:air")))
                        for entry in palette
                    ]

                    if len(names) == 1:
                        indices = [0] * 4096
                    elif data is not None:
                        bits = max(4, math.ceil(math.log2(len(names))))
                        indices = unpack_states(data, bits)
                    else:
                        continue

                    for i, palette_index in enumerate(indices):
                        name = names[palette_index]
                        if name == "minecraft:air":
                            continue

                        lx = i % 16
                        lz = (i // 16) % 16
                        ly = i // 256

                        x = cx * 16 + lx
                        y = section_y * 16 + ly
                        z = cz * 16 + lz

                        chunk_counts[name] += 1
                        total_blocks[name] += 1
                        chunk_positions.append((x, y, z))

                        block_rows.append((x, y, z, name))

                if chunk_positions:
                    xs = [p[0] for p in chunk_positions]
                    ys = [p[1] for p in chunk_positions]
                    zs = [p[2] for p in chunk_positions]

                    chunk_rows.append({
                        "chunk_x": cx,
                        "chunk_z": cz,
                        "blocks": len(chunk_positions),
                        "min_x": min(xs),
                        "max_x": max(xs),
                        "min_y": min(ys),
                        "max_y": max(ys),
                        "min_z": min(zs),
                        "max_z": max(zs),
                        "top_blocks": "; ".join(
                            f"{name}: {count}"
                            for name, count in chunk_counts.most_common(10)
                        )
                    })

            except Exception as error:
                print(f"Chunk slot {slot}: {error}")

    with open(os.path.join(args.output, "chunks.csv"), "w", newline="", encoding="utf-8") as f:
        fields = [
            "chunk_x", "chunk_z", "blocks",
            "min_x", "max_x", "min_y", "max_y",
            "min_z", "max_z", "top_blocks"
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(chunk_rows)

    with open(os.path.join(args.output, "blocks.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["x", "y", "z", "block"])
        writer.writerows(block_rows)

    with open(os.path.join(args.output, "summary.txt"), "w", encoding="utf-8") as f:
        f.write(f"Region: {region_name}\n")
        f.write(f"Chunks with blocks: {len(chunk_rows)}\n")
        f.write(f"Total non-air blocks: {sum(total_blocks.values())}\n\n")
        f.write("Block counts:\n")
        for name, count in total_blocks.most_common():
            f.write(f"{name}: {count}\n")

    print(f"Finished. Chunks: {len(chunk_rows)}")
    print(f"Blocks: {sum(total_blocks.values())}")


if __name__ == "__main__":
    main()
