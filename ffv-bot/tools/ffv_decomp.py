"""FFV ROM decompressors, from the wiki Compression page. Functions take bytes plus an offset and return bytes."""


def decompress(rom: bytes, off: int, headerless_lzss: bool = False) -> bytes:
	kind = 2 if headerless_lzss else rom[off]
	if not headerless_lzss:
		off += 1
	if kind == 0:
		n = rom[off] | rom[off + 1] << 8
		return rom[off + 2:off + 2 + n]
	if kind == 1:
		out = bytearray()
		while True:
			a = rom[off]; off += 1
			if a == 0:
				return bytes(out)
			if a < 0x80:
				out += bytes([rom[off]]) * a; off += 1
			else:
				n = a & 0x7F; out += rom[off:off + n]; off += n
	if kind == 2:
		n = rom[off] | rom[off + 1] << 8
		off += 2
		ring = bytearray(0x800); pos = 0x7DE; out = bytearray()
		while len(out) < n:
			bm = rom[off]; off += 1
			for bit in range(8):
				if len(out) >= n:
					break
				if bm >> bit & 1:
					b = rom[off]; off += 1
					ring[pos] = b; pos = (pos + 1) & 0x7FF; out.append(b)
				else:
					lo, hi = rom[off], rom[off + 1]; off += 2
					o = lo | (hi >> 5) << 8
					for _ in range((hi & 0x1F) + 3):
						b = ring[o]; o = (o + 1) & 0x7FF
						ring[pos] = b; pos = (pos + 1) & 0x7FF; out.append(b)
		return bytes(out[:n])
	raise ValueError("unknown type %d" % kind)


def world_row(data: bytes, off: int) -> bytes:
	out = bytearray()
	while len(out) < 256:
		b = data[off]; off += 1
		if b > 0xBF:
			out += bytes([data[off]]) * (b - 0xBF); off += 1
		elif b in (0x0C, 0x1C, 0x2C):
			out += bytes([b, b + 1, b + 2])
		else:
			out.append(b)
	return bytes(out[:256])
