from pathlib import Path
import subprocess, re, os, json

root = Path.cwd()
src = root / "CryptoLib"
logs = root / "validation-logs"
logs.mkdir(exist_ok=True)
build = root / "build"
base = "626dc621f532dce3f146a5f97998152987d19237"
paths = ["src/sa/internal/sa_interface_inmemory.template.c", "test/unit/ut_ep_sa_mgmt.c"]
fixed = {p: (src / p).read_bytes() for p in paths}
original = {p: subprocess.check_output(["git", "show", base + ":" + p], cwd=src) for p in paths}
options = ["-DMC_INTERNAL=ON", "-DKEY_INTERNAL=ON", "-DSA_INTERNAL=ON", "-DCRYPTO_LIBGCRYPT=ON",
           "-DTEST=ON", "-DCRYPTO_EPROC=ON", "-DCMAKE_BUILD_TYPE=Debug"]

def run(label, args, cwd=None, allowed=(0,), env=None):
    p = subprocess.run(args, cwd=cwd, capture_output=True, env=env)
    text = (p.stdout + p.stderr).decode(errors="replace")
    (logs / (label + ".log")).write_text(text)
    assert p.returncode in allowed, label + "\n" + text[-4000:]
    return p.returncode, text

def rebuild(label):
    run(label, ["cmake", "--build", str(build), "-j4"])

def full(label):
    rc, text = run(label, ["ctest", "--test-dir", str(build), "--no-tests=error", "-V"], allowed=(0, 8))
    failed = set(re.findall(r"\[  FAILED  \] ([A-Za-z_][\w./]*)", text))
    assert rc == 0 or failed, "CTest failed without a recognized failing case"
    print(label, "failing cases", sorted(failed), flush=True)
    return failed

run("configure", ["cmake", "-S", str(src), "-B", str(build)] + options + ["-DDEBUG=ON", "-DSA_FILE=ON"])
try:
    rebuild("fixed-build")
    f = full("fixed-full")
    for p, content in original.items():
        (src / p).write_bytes(content)
    rebuild("untouched-build")
    b = full("untouched-full")
    assert f == b, (f, b)
    allowed_aos = {"AOS_APPLY.HAPPY_PATH_CLEAR_FECF_LEFT_BLANK", "AOS_APPLY.HAPPY_PATH_CLEAR_FHEC_FECF",
                   "AOS_APPLY.HAPPY_PATH_CLEAR_FHEC_OID_FECF", "AOS_APPLY.AES_CMAC_256_TEST_BITMASK_1",
                   "AOS_APPLY.AES_CMAC_256_TEST_BITMASK_0", "AOS_APPLY.AES_GCM"}
    assert f <= allowed_aos, f
    (src / paths[1]).write_bytes(fixed[paths[1]])
    rebuild("original-newtests-build")
    rc, text = run("original-newtests", [str(build / "bin/ut_ep_sa_mgmt"), "--filter=EpSaDelete.*"],
                   cwd=build, allowed=(6,))
    failures = set(re.findall(r"\[  FAILED  \] ([A-Za-z_][\w./]*)", text))
    assert len(failures) == 6, failures
    print("Original deletion fails six new cases; existing rejection controls pass.", flush=True)
finally:
    for p, content in fixed.items():
        (src / p).write_bytes(content)
rebuild("restored-build")
assert full("restored-full") == f
rc, text = run("restored-ep", ["ctest", "--test-dir", str(build), "--no-tests=error", "-V", "-R", "^UT_EP_SA_MGMT$"])
assert "[  PASSED  ] 26 tests." in text
asan = root / "build-asan"
run("sanitizer-configure", ["cmake", "-S", str(src), "-B", str(asan)] + options + [
    "-DDEBUG=OFF", "-DSA_FILE=OFF", "-DCMAKE_C_FLAGS=-fsanitize=address,undefined -fno-omit-frame-pointer",
    "-DCMAKE_EXE_LINKER_FLAGS=-fsanitize=address,undefined", "-DCMAKE_SHARED_LINKER_FLAGS=-fsanitize=address,undefined"])
run("sanitizer-build", ["cmake", "--build", str(asan), "--target", "ut_ep_sa_mgmt", "-j4"])
env = os.environ.copy()
env.update(ASAN_OPTIONS="detect_leaks=1:halt_on_error=1", UBSAN_OPTIONS="halt_on_error=1")
rc, text = run("sanitizer-ep", [str(asan / "bin/ut_ep_sa_mgmt")], cwd=asan, env=env)
assert "[  PASSED  ] 26 tests." in text
assert not re.search(r"ERROR: AddressSanitizer|runtime error:|LeakSanitizer:", text)
run("clean-source", ["git", "diff", "--exit-code"], cwd=src)
(logs / "summary.json").write_text(json.dumps({"unchanged_full_suite_failures": sorted(f),
    "ep_cases_passed": 26, "sanitized_ep_cases_passed": 26, "original_regression_failures": 6}, indent=2))
print("All 26 EP SA tests pass with and without sanitizers. Full-suite failures exactly match untouched upstream.", flush=True)
