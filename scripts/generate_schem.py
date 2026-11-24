
import argparse
import gzip
import math
import os
import struct
import zlib
from collections import OrderedDict
from io import BytesIO

import nbtlib
from nbtlib import ByteArray, Compound, Int, IntArray, List, Short, String


def decode_chunk(data):
    length = struct.unpack(">I", data[:4])[0]
    compression = data[4]
    payload = data[5:4 + length]

    if compression == 1:
        payload = gzip.decompress(payload)
    elif compression == 2:
        payload = zlib.decompress(payload)
    elif compression != 3:
        raise ValueError(f"Unsupported compression: {compression}")

    return nbtlib.File.parse(BytesIO(payload))


def unpack_states(data, bits, count=4096):
    values = []
    mask = (1 << bits) - 1
    per_long = 64 // bits

    for i in range(count):
        word = int(data[i // per_long]) & ((1 << 64) - 1)
        offset = (i % per_long) * bits
        values.append((word >> offset) & mask)

    return values


def block_state(entry):
    name = str(entry.get("Name", "minecraft:air"))
    props = entry.get("Properties")

    if props:
        pairs = sorted((str(k), str(v)) for k, v in props.items())
        name += "[" + ",".join(f"{k}={v}" for k, v in pairs) + "]"

    return name


def read_region(path):
    rx, rz = map(
        int,
        os.path.basename(path).replace(".mca", "").split(".")[1:]
    )

    blocks = {}
    data_version = 0

    with open(path, "rb") as f:
        header = f.read(8192)

        for slot in range(1024):
            loc = header[slot * 4:slot * 4 + 4]
            offset = int.from_bytes(loc[:3], "big")
            sectors = loc[3]

            if not offset or not sectors:
                continue

            f.seek(offset * 4096)
            raw = f.read(sectors * 4096)

            try:
                root = decode_chunk(raw)
                data_version = max(
                    data_version,
                    int(root.get("DataVersion", 0))
                )

                level = root.get("Level", root)
                cx = rx * 32 + slot % 32
                cz = rz * 32 + slot // 32

                sections = level.get(
                    "sections",
                    level.get("Sections", [])
                )

                for section in sections:
                    sy = int(section.get("Y", 0))
                    states = section.get(
                        "block_states",
                        section.get("BlockStates")
                    )

                    if states is None:
                        continue

                    palette = states.get(
                        "palette",
                        states.get("Palette")
                    )
                    packed = states.get(
                        "data",
                        states.get("BlockStates")
                    )

                    if not palette:
                        continue

                    names = [block_state(e) for e in palette]

                    if len(names) == 1:
                        indices = [0] * 4096
                    elif packed is not None:
                        bits = max(4, math.ceil(math.log2(len(names))))
                        indices = unpack_states(packed, bits)
                    else:
                        continue

                    for i, pi in enumerate(indices):
                        state = names[pi]

                        if state == "minecraft:air":
                            continue

                        lx = i % 16
                        lz = (i // 16) % 16
                        ly = i // 256

                        pos = (
                            cx * 16 + lx,
                            sy * 16 + ly,
                            cz * 16 + lz
                        )

                        blocks[pos] = state

            except Exception as exc:
                print(f"Skipping slot {slot}: {exc}")

    if not blocks:
        raise RuntimeError("No blocks extracted from region")

    return blocks, data_version


def write_schematic(blocks, data_version, output):
    xs = [p[0] for p in blocks]
    ys = [p[1] for p in blocks]
    zs = [p[2] for p in blocks]

    min_x, min_y, min_z = min(xs), min(ys), min(zs)

    width = max(xs) - min_x + 1
    height = max(ys) - min_y + 1
    length = max(zs) - min_z + 1

    if max(width, height, length) > 32767:
        raise RuntimeError("Schematic dimensions exceed format limits")

    palette = OrderedDict()
    palette["minecraft:air"] = 0

    for state in blocks.values():
        if state not in palette:
            palette[state] = len(palette)

    encoded = bytearray()

    for y in range(min_y, min_y + height):
        for z in range(min_z, min_z + length):
            for x in range(min_x, min_x + width):
                state_id = palette.get(
                    blocks.get((x, y, z), "minecraft:air"),
                    0
                )

                value = state_id

                while value & ~0x7F:
                    encoded.append((value & 0x7F) | 0x80)
                    value >>= 7

                encoded.append(value)

    signed_data = [
        b if b < 128 else b - 256
        for b in encoded
    ]

    root = Compound({
        "Version": Int(2),
        "DataVersion": Int(data_version),
        "Width": Short(width),
        "Height": Short(height),
        "Length": Short(length),
        "Offset": IntArray([min_x, min_y, min_z]),
        "PaletteMax": Int(len(palette)),
        "Palette": Compound({
            state: Int(index)
            for state, index in palette.items()
        }),
        "BlockData": ByteArray(signed_data),
        "BlockEntities": List[Compound](),
        "Metadata": Compound({
            "Name": String("Pale Construct")
        })
    })

    nbtlib.File(root, root_name="Schematic").save(
        output,
        gzipped=True
    )

    print(f"Created: {output}")
    print(f"Dimensions: {width} x {height} x {length}")
    print(f"Non-air blocks: {len(blocks):,}")
    print(f"Palette states: {len(palette):,}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    blocks, data_version = read_region(args.input)
    write_schematic(blocks, data_version, args.output)


if __name__ == "__main__":
    main()
