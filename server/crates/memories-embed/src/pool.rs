//! A small thread pool for the steps of the encoder.
//!
//! After a job, each worker spins for a short time before it sleeps (as the
//! pool of ONNX Runtime does). The steps of a short text take less than a
//! millisecond, and a thread that sleeps takes too long to wake for them.
#![allow(unsafe_code)]

use std::any::Any;
use std::panic::{AssertUnwindSafe, catch_unwind, resume_unwind};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Condvar, Mutex, MutexGuard, OnceLock};
use std::time::{Duration, Instant};

/// How long a worker spins after its last task before it sleeps.
const SPIN: Duration = Duration::from_millis(1);

type Task<'a> = dyn Fn(usize) + Sync + 'a;

/// The task of the current job, with its lifetime erased.
#[derive(Clone, Copy)]
struct TaskPtr(*const Task<'static>);
// Safety: the task is `Sync`, and `Pool::run` keeps it alive while a worker
// can call it (see `Pool::run`).
unsafe impl Send for TaskPtr {}

struct State {
    /// `Some` from the start of a job until all its calls are done.
    task: Option<TaskPtr>,
    /// The next index to give out, and the number of indexes of the job.
    next: usize,
    n: usize,
    /// Calls that started and did not end.
    running: usize,
    sleeping: usize,
    panic: Option<Box<dyn Any + Send>>,
    stop: bool,
}

struct Shared {
    state: Mutex<State>,
    wake: Condvar,
    /// Goes up by one, under the state lock, for each job and for the stop.
    /// A worker that spins reads it with no lock.
    epoch: AtomicUsize,
}

impl Shared {
    fn lock(&self) -> MutexGuard<'_, State> {
        // No code panics while it holds the lock, so a poisoned lock has good data.
        self.state.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Takes indexes of the current job and runs them, until none is left.
    fn work(&self) {
        let mut state = self.lock();
        while let Some(task) = state.task
            && state.next < state.n
        {
            let i = state.next;
            state.next += 1;
            state.running += 1;
            drop(state);
            // Safety: this thread took index `i` and added to `running` under
            // the lock while `task` was the current job. `Pool::run` returns
            // only after `running` is 0 again, so the closure is alive.
            let result = catch_unwind(AssertUnwindSafe(|| unsafe { (*task.0)(i) }));
            state = self.lock();
            state.running -= 1;
            if let Err(panic) = result {
                match state.panic {
                    // Not dropped: the drop of a payload can panic, and
                    // `Pool::run` must not unwind while its task is set.
                    Some(_) => std::mem::forget(panic),
                    None => state.panic = Some(panic),
                }
                // Do not start the calls that are left.
                state.next = state.n;
            }
        }
    }

    fn worker(&self) {
        let mut seen = 0;
        let mut idle_since = Instant::now();
        loop {
            if self.epoch.load(Ordering::Acquire) == seen {
                if idle_since.elapsed() < SPIN {
                    std::hint::spin_loop();
                    continue;
                }
                let mut state = self.lock();
                state.sleeping += 1;
                while self.epoch.load(Ordering::Acquire) == seen {
                    state = self.wake.wait(state).unwrap_or_else(|e| e.into_inner());
                }
                state.sleeping -= 1;
            }
            seen = self.epoch.load(Ordering::Acquire);
            if self.lock().stop {
                return;
            }
            self.work();
            idle_since = Instant::now();
        }
    }
}

pub struct Pool {
    shared: Arc<Shared>,
    /// One job at a time.
    caller: Mutex<()>,
    /// Worker threads that started. The caller of `run` works too.
    workers: usize,
}

impl Pool {
    /// A pool that runs a job on `threads` threads: the caller and
    /// `threads - 1` workers.
    pub fn new(threads: usize) -> Self {
        let shared = Arc::new(Shared {
            state: Mutex::new(State {
                task: None,
                next: 0,
                n: 0,
                running: 0,
                sleeping: 0,
                panic: None,
                stop: false,
            }),
            wake: Condvar::new(),
            epoch: AtomicUsize::new(0),
        });
        let workers = (1..threads)
            .filter(|i| {
                let shared = shared.clone();
                std::thread::Builder::new()
                    .name(format!("memories-embed-{i}"))
                    .spawn(move || shared.worker())
                    .inspect_err(|e| tracing::warn!("cannot start an embedder thread: {e}"))
                    .is_ok()
            })
            .count();
        Self {
            shared,
            caller: Mutex::new(()),
            workers,
        }
    }

    pub fn threads(&self) -> usize {
        self.workers + 1
    }

    /// Calls `f(0)` to `f(n - 1)` on the pool threads and on this thread, and
    /// returns after the last call. If a call panics, the calls that did not
    /// start are skipped, and this function panics with the same payload.
    ///
    /// Do not call `run` of the same pool from `f`: that call waits for ever.
    pub fn run(&self, n: usize, f: &Task<'_>) {
        if n <= 1 || self.workers == 0 {
            return (0..n).for_each(f);
        }
        let _one_job = self.caller.lock().unwrap_or_else(|e| e.into_inner());
        let shared = &*self.shared;
        let mut state = shared.lock();
        // Safety: the lifetime of `f` is erased, so this function must not
        // return or unwind while a worker can call `f`. A worker reads `task`
        // and takes an index in one lock hold, and `running` counts each call
        // that did not end. From here to the line that clears `task`, no code
        // unwinds (`work` catches the panics of `f`), and that line runs only
        // after no index is left and `running` is 0.
        state.task = Some(TaskPtr(unsafe {
            std::mem::transmute::<*const Task<'_>, *const Task<'static>>(f)
        }));
        state.next = 0;
        state.n = n;
        shared.epoch.fetch_add(1, Ordering::Release);
        let wake = state.sleeping > 0;
        drop(state);
        if wake {
            shared.wake.notify_all();
        }
        shared.work();
        // The other threads are in their last call. Spin first: the wait is short.
        let mut tries = 0u32;
        let mut state = shared.lock();
        while state.running != 0 {
            drop(state);
            if tries < 10_000 {
                tries += 1;
                std::hint::spin_loop();
            } else {
                std::thread::yield_now();
            }
            state = shared.lock();
        }
        state.task = None;
        let panic = state.panic.take();
        drop(state);
        if let Some(panic) = panic {
            resume_unwind(panic);
        }
    }

    /// `[f(0), ..., f(n - 1)]`, with the calls on the pool as in [`Pool::run`].
    pub fn map<T: Send>(&self, n: usize, f: impl Fn(usize) -> T + Sync) -> Vec<T> {
        let slots: Vec<Mutex<Option<T>>> = (0..n).map(|_| Mutex::new(None)).collect();
        self.run(n, &|i| {
            *slots[i].lock().unwrap_or_else(|e| e.into_inner()) = Some(f(i));
        });
        slots
            .into_iter()
            .map(|slot| {
                slot.into_inner()
                    .unwrap_or_else(|e| e.into_inner())
                    .expect("run made each call")
            })
            .collect()
    }
}

impl Drop for Pool {
    fn drop(&mut self) {
        let mut state = self.shared.lock();
        state.stop = true;
        self.shared.epoch.fetch_add(1, Ordering::Release);
        drop(state);
        self.shared.wake.notify_all();
    }
}

/// The pool of the embedder: [`crate::INTRA_THREADS`] threads, or the number
/// of CPUs that the process can use if that is less.
pub fn global() -> &'static Pool {
    static POOL: OnceLock<Pool> = OnceLock::new();
    POOL.get_or_init(|| {
        let cpus = std::thread::available_parallelism().map_or(1, |n| n.get());
        Pool::new(crate::INTRA_THREADS.min(cpus))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn each_index_runs_one_time_in_many_short_jobs() {
        let pool = Pool::new(4);
        assert_eq!(pool.threads(), 4);
        // Miri runs each step about 1,000 times slower.
        let jobs = if cfg!(miri) { 60 } else { 5_000usize };
        for job in 0..jobs {
            let n = job % 37;
            // The closure borrows from this stack frame.
            let hits: Vec<AtomicUsize> = (0..n).map(|_| AtomicUsize::new(0)).collect();
            pool.run(n, &|i| {
                hits[i].fetch_add(1, Ordering::Relaxed);
            });
            assert!(
                hits.iter().all(|h| h.load(Ordering::Relaxed) == 1),
                "job {job}"
            );
        }
    }

    #[test]
    fn a_job_after_the_workers_sleep_still_runs() {
        let pool = Pool::new(3);
        for _ in 0..3 {
            std::thread::sleep(SPIN * 20);
            assert_eq!(
                pool.map(64, |i| i * 2),
                (0..64).map(|i| i * 2).collect::<Vec<_>>()
            );
        }
    }

    #[test]
    fn map_keeps_the_order_and_handles_the_small_sizes() {
        for threads in [1, 2, 4] {
            let pool = Pool::new(threads);
            assert_eq!(pool.map(0, |i| i), Vec::<usize>::new());
            assert_eq!(pool.map(1, |i| i + 5), [5]);
            assert_eq!(
                pool.map(100, |i| i * i),
                (0..100).map(|i| i * i).collect::<Vec<_>>()
            );
        }
    }

    #[test]
    fn a_panic_in_a_call_comes_back_to_the_caller_and_the_pool_still_works() {
        let pool = Pool::new(4);
        for round in 0..if cfg!(miri) { 4 } else { 50 } {
            let done = AtomicUsize::new(0);
            let result = catch_unwind(AssertUnwindSafe(|| {
                pool.run(200, &|i| {
                    if i == round {
                        panic!("call {i} failed");
                    }
                    done.fetch_add(1, Ordering::Relaxed);
                });
            }));
            let message = result.unwrap_err();
            assert_eq!(
                message.downcast_ref::<String>().unwrap(),
                &format!("call {round} failed")
            );
            assert!(done.load(Ordering::Relaxed) < 200);
            assert_eq!(pool.map(10, |i| i), (0..10).collect::<Vec<_>>());
        }
    }

    #[test]
    fn callers_on_many_threads_take_turns() {
        let pool = Pool::new(4);
        std::thread::scope(|scope| {
            for t in 0..4usize {
                let pool = &pool;
                scope.spawn(move || {
                    for job in 0..if cfg!(miri) { 10 } else { 500usize } {
                        let want: Vec<usize> = (0..20).map(|i| i + t * job).collect();
                        assert_eq!(pool.map(20, |i| i + t * job), want);
                    }
                });
            }
        });
    }
}
