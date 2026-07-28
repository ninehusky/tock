// Licensed under the Apache License, Version 2.0 or the MIT License.
// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright Tock Contributors 2022.

//! Capabilities for specifying capsule access to network resources
//!
//! A network capability specifies (1) with what IP addresses the holder of the
//! capability may communicate, (2) from which UDP ports the holder may send,
//! and (3) to which UDP ports the holder may send. In order to express various
//! ranges of IP addresses, one uses the AddrRange enum. One specifies ranges of
//! ports using the PortRange enum.
//!
//! Capsules must obtain static references to network capabilities from trusted
//! code (i.e. code that must use the unsafe keyword) since the constructor of
//! a network capability requires the NetworkCapabilityCreationCapability capability. Code that
//! checks these capabilities must possess the appropriate visibilty privileges.
//! UDP visibility privileges are given through the UdpVisibilityCapability capability and IP
//! visibility privileges are given through the IpVisibilityCapability capability.
//!
//! An example of the visibility capabilities can be found in udp_port_table.rs.
//! When attempting to bind to a port, we must first verify that the caller of
//! bind has a capability to send from that port. Therefore, we check the
//! network capability of the caller. In order to check the UDP-specific aspect
//! of the network capability, the port table must posses a UdpVisibilityCapability reference.
use crate::net::ipv6::ip_utils::IPAddr;

const MAX_ADDR_SET_SIZE: usize = 8;
const MAX_PORT_SET_SIZE: usize = 8;

use kernel::capabilities::NetworkCapabilityCreationCapability;

#[derive(Debug, Clone, Copy, PartialEq)]
#[flux_rs::refined_by()]
pub enum AddrRange {
    #[flux_rs::variant(AddrRange)]
    Any, // Any address
    #[flux_rs::variant(AddrRange)]
    NoAddrs,
    #[flux_rs::variant(([IPAddr; _]) -> AddrRange)]
    AddrSet([IPAddr; MAX_ADDR_SET_SIZE]),
    #[flux_rs::variant((IPAddr) -> AddrRange)]
    Addr(IPAddr),
    // The `<= 128` invariant is for `prefix_full_bytes`
    #[flux_rs::variant((IPAddr, usize{v: v <= 128}) -> AddrRange)]
    Subnet(IPAddr, usize), // address, prefix length (max 128)
}

/// An IPv6 prefix length split into whole bytes and leftover bits.
#[flux_rs::refined_by(full: int, rem: int)]
#[flux_rs::invariant(full <= 16 && rem < 8 && (rem != 0 => full < 16))]
struct PrefixSplit {
    #[field(usize[full])]
    full_bytes: usize,
    #[field(usize[rem])]
    remainder_bits: usize,
}

/// Trusted for one arithmetic step: I wonder why Z3 chokes on this?
#[flux_rs::trusted(reason = "arithmetic: p <= 128 && p % 8 != 0 => p <= 127 => p / 8 <= 15")]
#[flux_rs::sig(fn(p: usize{p <= 128}) -> PrefixSplit)]
fn split_prefix(prefix_len: usize) -> PrefixSplit {
    PrefixSplit {
        full_bytes: prefix_len / 8,
        remainder_bits: prefix_len % 8,
    }
}

impl AddrRange {
    pub fn is_addr_valid(&self, addr: IPAddr) -> bool {
        match self {
            AddrRange::Any => true,
            AddrRange::NoAddrs => false,
            AddrRange::AddrSet(allowed_addrs) => allowed_addrs.iter().any(|&a| a == addr),
            AddrRange::Addr(allowed_addr) => addr == *allowed_addr, //TODO: refs?
            AddrRange::Subnet(allowed_addr, prefix_len) => {
                // Same two values as before; `split_prefix` additionally carries
                // `remainder_bits != 0 => full_bytes < 16`.
                let split = split_prefix(*prefix_len);
                let full_bytes = split.full_bytes;
                let remainder_bits = split.remainder_bits;
                // initial bytes -- TODO: edge case
                if allowed_addr.0[0..full_bytes] != addr.0[0..full_bytes] {
                    false
                } else if remainder_bits == 0 {
                    true //this case is necessary bc right shifting a u8 by 8 bits is UB
                } else {
                    // FLUX-TODO addr=0x19e98 flavor=bounds
                    flux_support::assert(full_bytes < addr.0.len() && full_bytes < allowed_addr.0.len());
                    addr.0[full_bytes] >> (8 - remainder_bits)
                        == allowed_addr.0[full_bytes] >> (8 - remainder_bits)
                }
            }
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum PortRange {
    Any,
    NoPorts,
    PortSet([u16; MAX_PORT_SET_SIZE]),
    Range(u16, u16),
    Port(u16),
}

impl PortRange {
    pub fn is_port_valid(&self, port: u16) -> bool {
        match self {
            PortRange::Any => true,
            PortRange::NoPorts => false,
            PortRange::PortSet(allowed_ports) => allowed_ports.iter().any(|&p| p == port), // TODO: check refs
            PortRange::Range(low, high) => *low <= port && port <= *high,
            PortRange::Port(allowed_port) => port == *allowed_port,
        }
    }
}

/// The UdpVisibilityCapability and IpVisibilityCapability has an empty private
/// field to make it so the only way to create these structs is via a call to
/// `new` which requires a NetworkCapabilityCreationCapability.
pub struct UdpVisibilityCapability {
    _priv: (), // an empty private field
}

pub struct IpVisibilityCapability {
    _priv: (), // an empty private field
}

impl UdpVisibilityCapability {
    pub fn new(
        _create_net_cap: &dyn NetworkCapabilityCreationCapability,
    ) -> UdpVisibilityCapability {
        UdpVisibilityCapability { _priv: () }
    }
}

impl IpVisibilityCapability {
    pub fn new(
        _create_net_cap: &dyn NetworkCapabilityCreationCapability,
    ) -> IpVisibilityCapability {
        IpVisibilityCapability { _priv: () }
    }
}

/// The NetworkCapability specifies access to network resourcess across the UDP
/// and IP layers. Access to layer-specific information is mediated by the
/// UdpVsibilityCapability and the IpVisibilityCapability.
pub struct NetworkCapability {
    // can potentially add more
    remote_addrs: AddrRange, // IP addresses with which the holder may communicate
    remote_ports: PortRange, // ports to which the holder may send
    local_ports: PortRange,  // ports from which the holder may send
}

impl NetworkCapability {
    pub fn new(
        remote_addrs: AddrRange,
        remote_ports: PortRange,
        local_ports: PortRange,
        _create_net_cap: &dyn NetworkCapabilityCreationCapability,
    ) -> NetworkCapability {
        NetworkCapability {
            remote_addrs,
            remote_ports,
            local_ports,
        }
    }

    pub fn get_range(&self, _ip_cap: &'static IpVisibilityCapability) -> AddrRange {
        self.remote_addrs
    }

    pub fn remote_addr_valid(
        &self,
        remote_addr: IPAddr,
        _ip_cap: &'static IpVisibilityCapability,
    ) -> bool {
        self.remote_addrs.is_addr_valid(remote_addr)
    }

    pub fn get_remote_ports(&self, _udp_cap: &'static UdpVisibilityCapability) -> PortRange {
        self.remote_ports
    }

    pub fn get_local_ports(&self, _udp_cap: &'static UdpVisibilityCapability) -> PortRange {
        self.local_ports
    }

    pub fn remote_port_valid(
        &self,
        remote_port: u16,
        _udp_cap: &'static UdpVisibilityCapability,
    ) -> bool {
        self.remote_ports.is_port_valid(remote_port)
    }

    pub fn local_port_valid(
        &self,
        local_port: u16,
        _udp_cap: &'static UdpVisibilityCapability,
    ) -> bool {
        self.local_ports.is_port_valid(local_port)
    }
}
