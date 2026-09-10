package spine

import "hash/crc32"

// ankiCRC is IEEE CRC-32 starting at 0xFFFFFFFF with no final invert.
func ankiCRC(data []byte) uint32 {
	return ^crc32.ChecksumIEEE(data)
}
