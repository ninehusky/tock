use core::cell::Cell;

#[derive(Clone, Copy, PartialEq)]
#[flux_rs::refined_by(state_num: int)]
pub enum MapCellState {
    #[flux_rs::variant(MapCellState[0])]
    Uninit,
    #[flux_rs::variant(MapCellState[1])]
    Init,
    #[flux_rs::variant(MapCellState[2])]
    Borrowed,
}

#[flux_rs::refined_by(state_num: int)]
pub struct FluxCell {
    inner: Cell<MapCellState>,
    #[field(MapCellState[state_num])]
    state: MapCellState,
}

impl FluxCell {
    #[flux_rs::sig(fn(value: MapCellState[@n]) -> FluxCell[n])]
    pub const fn new(value: MapCellState) -> Self {
        Self {
            inner: Cell::new(value),
            state: value,
        }
    }

    #[flux_rs::sig(fn(self: &mut FluxCell[@m], value: MapCellState[@n]) ensures self: FluxCell[n])]
    pub fn set(&mut self, value: MapCellState) {
        self.inner.set(value);
        self.state = value;
    }

    #[flux_rs::sig(fn(self: &FluxCell[@n]) -> MapCellState[n])]
    pub fn get(&self) -> MapCellState {
        self.state
    }
}
