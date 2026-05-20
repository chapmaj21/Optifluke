import subprocess
import time
import signal
import sys
import os
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)-8s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")

WORKSPACE = os.path.dirname(os.path.abspath(__file__))

ALGOS = [
    ("dual", os.path.join(WORKSPACE, "dual_listing", "your_dual_listing_algo.py")),
    ("etf", os.path.join(WORKSPACE, "etf_future", "your_etf_future_algo.py")),
    ("options", os.path.join(WORKSPACE, "options_market_making", "your_options_mm_algo.py")),
    ("xarb", os.path.join(WORKSPACE, "cross_instrument_arb", "cross_arb.py")),
]

processes: dict[str, subprocess.Popen] = {}


def start_algo(name: str, path: str) -> subprocess.Popen | None:
    if not os.path.exists(path):
        logger.warning(f"{name}: file not found, skipping")
        return None
    logger.info(f"starting {name}")
    proc = subprocess.Popen(
        [sys.executable, "-u", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    return proc


def stream_output(name: str, proc: subprocess.Popen):
    for line in proc.stdout:
        print(f"[{name:>7}] {line}", end="")


def shutdown(signum, frame):
    logger.info("killing all algos")
    for name, proc in processes.items():
        proc.terminate()
    time.sleep(1)
    for name, proc in processes.items():
        if proc.poll() is None:
            proc.kill()
    sys.exit(0)


signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)


def main():
    import threading

    for name, path in ALGOS:
        proc = start_algo(name, path)
        if proc is None:
            continue
        processes[name] = proc
        t = threading.Thread(target=stream_output, args=(name, proc), daemon=True)
        t.start()
        time.sleep(2)

    logger.info(f"running: {list(processes.keys())}")

    while True:
        time.sleep(15)
        for name, proc in list(processes.items()):
            ret = proc.poll()
            if ret is not None:
                logger.warning(f"{name} exited with code {ret}, restarting in 3s")
                time.sleep(3)
                idx = next(i for i, (n, _) in enumerate(ALGOS) if n == name)
                new_proc = start_algo(name, ALGOS[idx][1])
                if new_proc:
                    processes[name] = new_proc
                    t = threading.Thread(target=stream_output, args=(name, new_proc), daemon=True)
                    t.start()


if __name__ == "__main__":
    main()
