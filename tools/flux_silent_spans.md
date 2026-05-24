# SILENT assert spans (Flux did not check these)

From `tools/negation_probe.json`. Total SILENT: 83 (closure 26, in-scope non-closure 57, out-of-scope 0).
`closure` count is a lower bound (detector misses match-arm-nested closures).


## Inside a closure (cell.map(|x| {…}) — confirmed Flux bug)  (26)

- `capsules/extra/src/ieee802154/driver.rs:1075` — `fn receive` — `offset + frame_len + USER_FRAME_METADATA_SIZE <= rbuf.len()`
- `capsules/extra/src/ieee802154/driver.rs:572` — `fn lookup_addr_long` — `self.num_neighbors.get() <= neighbors.len()`
- `capsules/extra/src/ieee802154/driver.rs:602` — `fn lookup_key` — `self.num_keys.get() <= keys.len()`
- `capsules/extra/src/mx25r6435f.rs:268` — `fn enable_write` — `txbuffer.len() > 0`
- `capsules/extra/src/mx25r6435f.rs:304` — `fn read_sector` — `txbuffer.len() > 3`
- `capsules/extra/src/mx25r6435f.rs:540` — `fn read_write_done` — `i + 4 < write_buffer.len() && (i + (page_index * PAGE_SIZE) as usize) < SECTOR_S`
- `capsules/extra/src/mx25r6435f.rs:605` — `fn alarm` — `write_buffer.len() > 0`
- `capsules/extra/src/tickv.rs:428` — `fn hash_done` — `self.unhashed_key_buffer.is_some()`
- `capsules/extra/src/tickv.rs:476` — `fn read_complete` — `self.key_buffer.is_some()`
- `capsules/extra/src/tickv.rs:478` — `fn read_complete` — `self.value_buffer.is_some()`
- `capsules/extra/src/tickv.rs:495` — `fn read_complete` — `self.key_buffer.is_some()`
- `capsules/extra/src/tickv.rs:497` — `fn read_complete` — `self.value_buffer.is_some()`
- `capsules/extra/src/tickv.rs:520` — `fn read_complete` — `self.key_buffer.is_some()`
- `capsules/extra/src/tickv.rs:522` — `fn read_complete` — `self.value_buffer.is_some()`
- `capsules/extra/src/tickv.rs:557` — `fn read_complete` — `self.key_buffer.is_some()`
- `capsules/extra/src/tickv.rs:559` — `fn read_complete` — `self.value_buffer.is_some()`
- `capsules/extra/src/tickv.rs:590` — `fn read_complete` — `self.key_buffer.is_some()`
- `capsules/extra/src/tickv.rs:628` — `fn write_complete` — `self.key_buffer.is_some()`
- `capsules/extra/src/tickv.rs:630` — `fn write_complete` — `self.value_buffer.is_some()`
- `capsules/extra/src/tickv.rs:642` — `fn write_complete` — `self.key_buffer.is_some()`
- `chips/nrf52840/src/ieee802154_radio.rs:1002` — `fn handle_interrupt` — `self.tx_buf.is_some()`
- `chips/nrf52840/src/ieee802154_radio.rs:1022` — `fn handle_interrupt` — `self.tx_buf.is_some()`
- `chips/nrf52840/src/ieee802154_radio.rs:1063` — `fn handle_interrupt` — `self.rx_buf.is_some()`
- `chips/nrf52840/src/ieee802154_radio.rs:1071` — `fn handle_interrupt` — `data_len < rbuf.len()`
- `chips/nrf52840/src/ieee802154_radio.rs:892` — `fn handle_interrupt` — `radio::PSDU_OFFSET + radio::MHR_FC_SIZE < ack_buf.len()`
- `chips/nrf52840/src/ieee802154_radio.rs:916` — `fn handle_interrupt` — `self.rx_buf.is_some()`

## In-scope, NOT a closure, still skipped (the larger/second issue)  (57)

- `capsules/extra/src/ieee802154/framer.rs:448` — `fn incoming_frame_security` — `buf.len() >= radio::PSDU_OFFSET + LQI_SIZE`
- `capsules/extra/src/mx25r6435f.rs:322` — `fn read_sector` — `rxbuffer.is_some()`
- `capsules/extra/src/net/ipv6/ip_utils.rs:191` — `fn compute_udp_checksum` — `i < payload.len()`
- `capsules/extra/src/net/ipv6/ip_utils.rs:196` — `fn compute_udp_checksum` — `i + 1 < payload.len()`
- `capsules/extra/src/net/ipv6/ipv6.rs:346` — `fn copy_subslice_into` — `i < dst.len() && i < src.len()`
- `capsules/extra/src/net/ipv6/ipv6.rs:423` — `fn encode` — `done.is_some()`
- `capsules/extra/src/net/ipv6/ipv6.rs:429` — `fn encode` — `done.is_some()`
- `capsules/extra/src/net/ipv6/ipv6.rs:445` — `fn encode` — `payload_length <= self.payload.len()`
- `capsules/extra/src/net/ipv6/ipv6.rs:585` — `fn encode` — `done.is_some()`
- `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:1211` — `fn decompress_multicast` — `*consumed + 1 < buf.len() && 2 < ip_addr.0.len()`
- `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:1225` — `fn decompress_multicast` — `*consumed + 6 <= buf.len()`
- `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:1330` — `fn decompress_iid_link_local` — `*consumed + 8 <= buf.len()`
- `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:1347` — `fn decompress_iid_link_local` — `*consumed + 2 <= buf.len()`
- `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:1416` — `fn decompress_iid_context` — `*consumed + 8 <= buf.len()`
- `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:1431` — `fn decompress_iid_context` — `*consumed + 2 <= buf.len()`
- `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:1495` — `fn decompress_udp_ports` — `*consumed + 1 <= *consumed + 3`
- `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:1496` — `fn decompress_udp_ports` — `*consumed + 3 <= buf.len()`
- `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:1501` — `fn decompress_udp_ports` — `*consumed <= *consumed + 2`
- `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:1502` — `fn decompress_udp_ports` — `*consumed + 2 <= buf.len()`
- `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:1510` — `fn decompress_udp_ports` — `*consumed <= *consumed + 2`
- `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:1511` — `fn decompress_udp_ports` — `*consumed + 2 <= buf.len()`
- `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:1514` — `fn decompress_udp_ports` — `*consumed + 2 <= *consumed + 4`
- `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:1515` — `fn decompress_udp_ports` — `*consumed + 4 <= buf.len()`
- `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:1570` — `fn decompress_udp_checksum` — `*consumed <= *consumed + 2`
- `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:1571` — `fn decompress_udp_checksum` — `*consumed + 2 <= buf.len()`
- `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:665` — `fn decompress_ext_hdr` — `2 + len <= next_headers.len() && *consumed + len <= buf.len()`
- `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:992` — `fn decompress_tf` — `*consumed + 2 < buf.len()`
- `capsules/extra/src/net/sixlowpan/sixlowpan_state.rs:275` — `fn set_frag_hdr` — `hdr.len() >= 5`
- `capsules/extra/src/net/sixlowpan/sixlowpan_state.rs:283` — `fn set_frag_hdr` — `2 <= hdr.len()`
- `capsules/extra/src/net/sixlowpan/sixlowpan_state.rs:285` — `fn set_frag_hdr` — `0 < hdr.len()`
- `capsules/extra/src/net/sixlowpan/sixlowpan_state.rs:287` — `fn set_frag_hdr` — `4 <= hdr.len()`
- `capsules/extra/src/net/sixlowpan/sixlowpan_state.rs:290` — `fn set_frag_hdr` — `4 < hdr.len()`
- `capsules/extra/src/net/sixlowpan/sixlowpan_state.rs:592` — `fn prepare_next_fragment` — `payload_offset + payload_len <= ip6_packet.get_payload().len()`
- `capsules/extra/src/net/sixlowpan/sixlowpan_state.rs:771` — `fn receive_next_frame` — `payload_len <= payload.len()`
- `capsules/extra/src/net/sixlowpan/sixlowpan_state.rs:788` — `fn receive_next_frame` — `dgram_offset + payload_len <= packet.len() && payload_len <= payload.len()`
- `capsules/extra/src/net/sixlowpan/sixlowpan_state.rs:856` — `fn slice_view` — `off + len <= buf.len()`
- `capsules/extra/src/net/sixlowpan/sixlowpan_state.rs:955` — `fn receive_frame` — `packet.len() >= 5`
- `capsules/extra/src/net/stream.rs:277` — `fn encode_bytes` — `bs.len() <= buf.len()`
- `capsules/extra/src/net/udp/driver.rs:88` — `fn result_to_errorcode` — `rc_into.is_ok()`
- `capsules/extra/src/tickv.rs:247` — `fn read_region` — `self.flash_read_buffer.is_some()`
- `capsules/extra/src/tickv.rs:419` — `fn add_mut_data_done` — `self.key_buffer.is_some()`
- `capsules/extra/src/virtual_kv.rs:126` — `fn insert` — `self.key.is_some() && self.value.is_some()`
- `capsules/extra/src/virtual_kv.rs:163` — `fn get` — `self.key.is_some() && self.value.is_some()`
- `capsules/extra/src/virtual_kv.rs:231` — `fn delete` — `self.key.is_some()`
- `chips/nrf52/src/ble_radio.rs:636` — `fn handle_interrupt` — `self.buffer.is_some()`
- `chips/nrf52840/src/ieee802154_radio.rs:1047` — `fn handle_interrupt` — `self.tx_buf.is_some()`
- `chips/nrf52840/src/ieee802154_radio.rs:760` — `fn rx` — `self.rx_buf.is_some()`
- `chips/nrf52840/src/ieee802154_radio.rs:841` — `fn handle_interrupt` — `self.rx_buf.is_some()`
- `chips/nrf52840/src/ieee802154_radio.rs:979` — `fn handle_interrupt` — `self.timer0.is_some()`
- `chips/nrf52840/src/ieee802154_radio.rs:982` — `fn handle_interrupt` — `self.timer0.is_some()`
- `libraries/tickv/src/async_ops.rs:364` — `fn continue_operation` — `self.key.get().is_some()`
- `libraries/tickv/src/async_ops.rs:368` — `fn continue_operation` — `value_opt.is_some()`
- `libraries/tickv/src/async_ops.rs:372` — `fn continue_operation` — `self.key.get().is_some()`
- `libraries/tickv/src/async_ops.rs:382` — `fn continue_operation` — `buf_opt.is_some()`
- `libraries/tickv/src/async_ops.rs:385` — `fn continue_operation` — `self.key.get().is_some()`
- `libraries/tickv/src/async_ops.rs:394` — `fn continue_operation` — `self.key.get().is_some()`
- `libraries/tickv/src/async_ops.rs:396` — `fn continue_operation` — `self.key.get().is_some()`

## Out of include scope (expected — fn not in include filter)  (0)


## DEAD_VACUOUS — assert(false) sentinels whose body Flux doesn't check (19)

- `arch/cortex-m/src/lib.rs:154` — `fn unhandled_interrupt`
- `arch/cortex-v7m/src/lib.rs:300` — `fn hard_fault_handler_arm_v7m_kernel`
- `arch/cortex-v7m/src/lib.rs:352` — `fn hard_fault_handler_arm_v7m_kernel`
- `capsules/core/src/process_console.rs:1009` — `fn read_command`
- `capsules/core/src/virtualizers/virtual_aes_ccm.rs:504` — `fn start_ccm_encrypt`
- `capsules/core/src/virtualizers/virtual_aes_ccm.rs:880` — `fn crypt_done`
- `capsules/extra/src/ieee802154/framer.rs:200` — `fn ccm_encrypt_ranges`
- `capsules/extra/src/ieee802154/framer.rs:206` — `fn ccm_encrypt_ranges`
- `capsules/extra/src/net/ipv6/ipv6.rs:434` — `fn encode`
- `capsules/extra/src/net/ipv6/ipv6.rs:516` — `fn get_total_hdr_size`
- `capsules/extra/src/net/ipv6/ipv6.rs:546` — `fn set_transport_checksum`
- `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:1361` — `fn decompress_iid_link_local`
- `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:1448` — `fn decompress_iid_context`
- `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:905` — `fn decompress`
- `capsules/extra/src/tickv.rs:609` — `fn read_complete`
- `capsules/extra/src/tickv.rs:647` — `fn write_complete`
- `capsules/extra/src/tickv.rs:684` — `fn erase_complete`
- `libraries/tickv/src/async_ops.rs:402` — `fn continue_operation`
- `libraries/tickv/src/tickv.rs:455` — `fn append_key`
