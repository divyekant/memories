//! Python iteration order, where the rank rules depend on it.
//!
//! Ties in the graph step keep the order of a Python `dict` (insertion order)
//! and of a Python `set` of ints (hash table order). `PySet` copies the table
//! of CPython 3.11 `setobject.c` for int keys with no deletes.

use std::collections::HashMap;

/// A map that iterates in insertion order, as a Python `dict`.
#[derive(Clone, Debug, Default)]
pub struct OrderedMap<V> {
    keys: Vec<i64>,
    index: HashMap<i64, usize>,
    values: Vec<V>,
}

impl<V> OrderedMap<V> {
    pub fn new() -> Self {
        Self {
            keys: Vec::new(),
            index: HashMap::new(),
            values: Vec::new(),
        }
    }

    pub fn len(&self) -> usize {
        self.keys.len()
    }

    pub fn is_empty(&self) -> bool {
        self.keys.is_empty()
    }

    pub fn get(&self, key: i64) -> Option<&V> {
        self.index.get(&key).map(|&i| &self.values[i])
    }

    pub fn get_mut(&mut self, key: i64) -> Option<&mut V> {
        self.index.get(&key).map(|&i| &mut self.values[i])
    }

    pub fn contains(&self, key: i64) -> bool {
        self.index.contains_key(&key)
    }

    /// The value for `key`; a new key goes to the end with `default()`.
    pub fn entry(&mut self, key: i64, default: impl FnOnce() -> V) -> &mut V {
        let i = *self.index.entry(key).or_insert_with(|| {
            self.keys.push(key);
            self.values.push(default());
            self.keys.len() - 1
        });
        &mut self.values[i]
    }

    pub fn iter(&self) -> impl Iterator<Item = (i64, &V)> {
        self.keys.iter().copied().zip(self.values.iter())
    }

    pub fn keys(&self) -> impl Iterator<Item = i64> + '_ {
        self.keys.iter().copied()
    }
}

impl<V> FromIterator<(i64, V)> for OrderedMap<V> {
    /// As `dict(pairs)`: a repeated key keeps its first position and its last value.
    fn from_iter<I: IntoIterator<Item = (i64, V)>>(iter: I) -> Self {
        let mut map = Self::new();
        for (k, v) in iter {
            match map.get_mut(k) {
                Some(slot) => *slot = v,
                None => {
                    map.entry(k, || v);
                }
            }
        }
        map
    }
}

const MIN_SIZE: usize = 8;
const LINEAR_PROBES: usize = 9;
const PERTURB_SHIFT: u32 = 5;

/// `hash(int)` of CPython, as `size_t`.
fn py_hash(x: i64) -> u64 {
    const MODULUS: i128 = (1 << 61) - 1;
    let h = i128::from(x).signum() * (i128::from(x).abs() % MODULUS);
    let h = if h == -1 { -2 } else { h as i64 };
    h as u64
}

/// A Python `set` of ints that iterates in CPython table order.
#[derive(Clone, Debug)]
pub struct PySet {
    table: Vec<Option<i64>>,
    used: usize,
}

impl Default for PySet {
    fn default() -> Self {
        Self {
            table: vec![None; MIN_SIZE],
            used: 0,
        }
    }
}

impl PySet {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn len(&self) -> usize {
        self.used
    }

    pub fn is_empty(&self) -> bool {
        self.used == 0
    }

    /// The slot that holds `key`, or the free slot where `add` puts it.
    fn probe(table: &[Option<i64>], key: i64) -> usize {
        let mask = table.len() - 1;
        let hash = py_hash(key);
        let mut perturb = hash;
        let mut i = (hash as usize) & mask;
        loop {
            let probes = if i + LINEAR_PROBES <= mask {
                LINEAR_PROBES
            } else {
                0
            };
            for (j, slot) in table.iter().enumerate().skip(i).take(probes + 1) {
                match slot {
                    None => return j,
                    Some(k) if *k == key => return j,
                    Some(_) => {}
                }
            }
            perturb >>= PERTURB_SHIFT;
            i = (i
                .wrapping_mul(5)
                .wrapping_add(1)
                .wrapping_add(perturb as usize))
                & mask;
        }
    }

    pub fn contains(&self, key: i64) -> bool {
        self.table[Self::probe(&self.table, key)].is_some()
    }

    pub fn add(&mut self, key: i64) {
        let slot = Self::probe(&self.table, key);
        if self.table[slot].is_some() {
            return;
        }
        self.table[slot] = Some(key);
        self.used += 1;
        // With no deletes, fill equals used.
        let mask = self.table.len() - 1;
        if self.used * 5 >= mask * 3 {
            let min_used = if self.used > 50_000 {
                self.used * 2
            } else {
                self.used * 4
            };
            let mut size = MIN_SIZE;
            while size <= min_used {
                size <<= 1;
            }
            let old = std::mem::replace(&mut self.table, vec![None; size]);
            for k in old.into_iter().flatten() {
                let slot = Self::probe(&self.table, k);
                self.table[slot] = Some(k);
            }
        }
    }

    pub fn iter(&self) -> impl Iterator<Item = i64> + '_ {
        self.table.iter().filter_map(|k| *k)
    }

    /// `self & other`: walks the smaller set (`other` when sizes are equal).
    pub fn intersection(&self, other: &PySet) -> PySet {
        let (big, small) = if other.len() > self.len() {
            (other, self)
        } else {
            (self, other)
        };
        let mut out = PySet::new();
        for k in small.iter() {
            if big.contains(k) {
                out.add(k);
            }
        }
        out
    }

    /// `bool(self & other)`: walks the smaller set.
    pub fn intersects(&self, other: &PySet) -> bool {
        let (big, small) = if other.len() > self.len() {
            (other, self)
        } else {
            (self, other)
        };
        small.iter().any(|k| big.contains(k))
    }
}

impl FromIterator<i64> for PySet {
    fn from_iter<I: IntoIterator<Item = i64>>(iter: I) -> Self {
        let mut s = PySet::new();
        for k in iter {
            s.add(k);
        }
        s
    }
}
