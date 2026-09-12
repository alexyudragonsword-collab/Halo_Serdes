"""IBIS-AMI model + COM adapter seam.

An IBIS-AMI model is a shared library exposing three C entry points:

* ``AMI_Init(impulse, ..., params_in) -> impulse', params_out`` — the *Init
  flow*. LTI models (a Tx FIR, an Rx CTLE) do their entire job here by
  transforming the channel impulse response.
* ``AMI_GetWave(wave, ..., params) -> wave', clock_times`` — the *GetWave
  flow*. Non-LTI models (a DFE, a CDR, an AGC) process a time-domain block
  and may emit recovered clock edges.
* ``AMI_Close`` — teardown.

:class:`AmiModel` presents those two flows as one Python interface so the time
engine can host a Tx or Rx model without caring whether it is a native
behavioral block or a vendor ``.so``/``.dll`` loaded through ``pyibisami``.
:class:`NativeFirAmi` is a dependency-free reference implementation of both
flows (used in tests and as a template); :class:`IbisAmiModel` binds a real
model through ``pyibisami`` and raises a clear error if that backend is absent.

:class:`ComAdapter` is the sibling seam for Channel Operating Margin.
:class:`NativeCom` computes a simplified behavioral COM from the pulse
response; the official IEEE 802.3 (93A/178A) tool plugs in behind the same
``compute`` signature.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..core.sampler import upsampled_taps

_AMI_C_DIR = Path(__file__).resolve().parent / "ami_c"

# --------------------------------------------------------------------------- #
# AMI model seam
# --------------------------------------------------------------------------- #


class AmiModel(ABC):
    """Uniform Tx/Rx model interface mirroring the IBIS-AMI two-flow contract.

    Subclasses implement whichever flow(s) they support. ``has_getwave``
    tells the host whether the GetWave (time-domain) flow is available;
    Init-only LTI models leave :meth:`get_wave` at its passthrough default.
    """

    #: True when the model implements the time-domain GetWave flow.
    has_getwave: bool = False
    #: Free-text messages the model returned (AMI ``msg`` out-parameter).
    messages: str = ""

    @abstractmethod
    def init(self, impulse: np.ndarray, dt: float, ui: float,
             **params: object) -> np.ndarray:
        """Init flow: return the model's transform of ``impulse`` (V/sample).

        LTI models realize their whole response here. Non-LTI models return
        the impulse unchanged (or lightly conditioned) and do their work in
        :meth:`get_wave`.
        """

    def get_wave(self, wave: np.ndarray, dt: float,
                 ui: float) -> tuple[np.ndarray, np.ndarray | None]:
        """GetWave flow: return ``(processed_wave, clock_times|None)``.

        Default is an identity passthrough for Init-only models.
        """
        return np.asarray(wave, dtype=np.float64), None

    def close(self) -> None:  # noqa: D401 - teardown hook, usually a no-op
        """Release any backend resources (AMI_Close)."""


class NativeFirAmi(AmiModel):
    """Dependency-free reference AMI model: a symbol- or sample-spaced FIR.

    Serves two purposes: it exercises the :class:`AmiModel` seam end-to-end in
    tests without any external package, and it is a worked template for a real
    behavioral EQ block. Both flows are implemented and are numerically
    consistent (GetWave on an impulse reproduces the Init transform).

    Args:
        taps: FIR taps ordered ``[pre..., main, post...]``.
        n_pre: number of precursor taps.
        sample_spaced: if True the taps are dt-spaced (apply directly to the
            oversampled wave); if False they are UI-spaced and are expanded to
            the oversample grid at :meth:`get_wave` time via ``osr = ui/dt``.
    """

    has_getwave = True

    def __init__(self, taps, n_pre: int = 0, sample_spaced: bool = True):
        self.taps = np.asarray(taps, dtype=np.float64)
        self.n_pre = int(n_pre)
        self.sample_spaced = bool(sample_spaced)

    def _grid_taps(self, dt: float, ui: float) -> np.ndarray:
        if self.sample_spaced:
            return self.taps
        osr = int(round(ui / dt))
        return upsampled_taps(self.taps, osr)

    def init(self, impulse: np.ndarray, dt: float, ui: float,
             **params: object) -> np.ndarray:
        h = np.asarray(impulse, dtype=np.float64)
        t = self._grid_taps(dt, ui)
        pre = self.n_pre * (1 if self.sample_spaced else int(round(ui / dt)))
        full = np.convolve(h, t)
        return full[pre: pre + h.size]

    def get_wave(self, wave: np.ndarray, dt: float,
                 ui: float) -> tuple[np.ndarray, np.ndarray | None]:
        y = np.asarray(wave, dtype=np.float64)
        t = self._grid_taps(dt, ui)
        pre = self.n_pre * (1 if self.sample_spaced else int(round(ui / dt)))
        full = np.convolve(y, t)
        return full[pre: pre + y.size], None


class IbisAmiModel(AmiModel):
    """Bind a real IBIS-AMI model through ``pyibisami``.

    Loading is lazy: the ``pyibisami`` import and the shared-library dlopen
    happen in the constructor, which raises a clear, actionable error if the
    backend is not installed. Once constructed, :meth:`init` and
    :meth:`get_wave` delegate to the model's ``AMI_Init`` / ``AMI_GetWave``.

    Args:
        ami_file: path to the ``.ami`` parameter definition file.
        dll_file: path to the model shared object (``.so``/``.dll``/``.dylib``).
        params: AMI input-parameter overrides (leaf name -> value).
    """

    def __init__(self, ami_file: str, dll_file: str,
                 params: dict | None = None):
        try:
            from pyibisami.ami.model import AMIModel  # type: ignore
            from pyibisami.ami.parameter import AMIParamConfigurator  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on optional pkg
            raise ImportError(
                "IbisAmiModel needs the optional 'pyibisami' backend. "
                "Install it with `pip install pyibisami` (or the project's "
                "[ami] extra). The AmiModel interface and NativeFirAmi work "
                "without it."
            ) from exc
        self._AMIModel = AMIModel
        self._cfg = AMIParamConfigurator(ami_file)
        if params:
            for k, v in params.items():
                try:
                    self._cfg.set_param_val(k, v)
                except Exception:  # pragma: no cover - passthrough
                    pass
        self._model = AMIModel(dll_file)
        info = getattr(self._model, "info_params", None) or {}
        # default True: most Rx models implement GetWave; overridden by the
        # model's own GetWave_Exists reserved param when present.
        self.has_getwave = bool(info.get("GetWave_Exists", True)) \
            if hasattr(info, "get") else True
        self._ami_file = ami_file
        self._dll_file = dll_file

    def _param_str(self) -> str:
        try:
            return self._cfg.input_ami_params  # pyibisami-rendered param string
        except Exception:  # pragma: no cover
            return "(halo_serdes)"

    def init(self, impulse: np.ndarray, dt: float, ui: float,
             **params: object) -> np.ndarray:  # pragma: no cover - needs backend
        from pyibisami.ami.model import AMIModelInitializer  # type: ignore

        h = np.asarray(impulse, dtype=np.float64)
        bit_time = ui
        init = AMIModelInitializer(
            self._cfg.ami_parsing_errors if False else {},
            info_params=getattr(self._cfg, "info_ami_params", {}))
        init.sample_interval = dt
        init.bit_time = bit_time
        init.channel_response = h
        self._model.initialize(init)
        self.messages = getattr(self._model, "msg", "") or ""
        out = np.asarray(self._model.channel_response, dtype=np.float64)
        # keep the same length as the input impulse
        if out.size >= h.size:
            return out[: h.size]
        return np.pad(out, (0, h.size - out.size))

    def get_wave(self, wave: np.ndarray, dt: float,
                 ui: float) -> tuple[np.ndarray, np.ndarray | None]:  # pragma: no cover
        y = np.asarray(wave, dtype=np.float64)
        out = self._model.getWave(y, int(round(ui / dt)))
        if isinstance(out, tuple):
            wav = np.asarray(out[0], dtype=np.float64)
            clk = np.asarray(out[1], dtype=np.float64) if len(out) > 1 else None
            return wav, clk
        return np.asarray(out, dtype=np.float64), None


def _shared_ext() -> str:
    if os.name == "nt":
        return ".dll"
    return ".dylib" if sys.platform == "darwin" else ".so"


def build_reference_ami(out_dir: str | None = None, *, cc: str | None = None
                        ) -> tuple[str, str]:
    """Compile the shipped reference AMI model to a shared object.

    Compiles ``io/ami_c/halo_fir_ami.c`` — a real IBIS-AMI model with the three
    spec C entry points — into a loadable ``.so``/``.dll``/``.dylib`` and returns
    ``(shared_object_path, ami_file_path)``. The result loads through
    :class:`AmiCModel`, which drives it over the actual AMI C ABI. Requires a C
    compiler (``cc`` or ``$CC``); raises :class:`RuntimeError` if absent or on a
    compile error.
    """
    src = _AMI_C_DIR / "halo_fir_ami.c"
    ami = _AMI_C_DIR / "halo_fir.ami"
    if not src.exists():
        raise RuntimeError(f"reference AMI source missing: {src}")
    cc = cc or os.environ.get("CC") or "cc"
    out = Path(out_dir) if out_dir else _AMI_C_DIR
    out.mkdir(parents=True, exist_ok=True)
    so = out / ("halo_fir_ami" + _shared_ext())
    cmd = [cc, "-shared", "-fPIC", "-O2", "-o", str(so), str(src), "-lm"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"C compiler {cc!r} not found") from exc
    if r.returncode != 0:
        raise RuntimeError(f"AMI model compile failed:\n{r.stderr}")
    return str(so), str(ami)


class AmiCModel(AmiModel):
    """Run a compiled IBIS-AMI model through the real C ABI via ``ctypes``.

    This is genuine IBIS-AMI *execution*: the ``.so``/``.dll`` is loaded and its
    ``AMI_Init`` / ``AMI_GetWave`` / ``AMI_Close`` entry points are called with
    the spec's C signatures — the same interface a vendor model exposes — with no
    ``pyibisami`` dependency. Pair it with :func:`build_reference_ami` (the
    shipped reference model) or point it at any spec-compliant shared object.

    Args:
        so_file: path to the compiled AMI shared object.
        taps / n_pre: convenience — rendered into the AMI parameter string
            ``(halo_fir (taps ...) (n_pre k))`` the reference model parses.
        params: a raw AMI parameter string, overriding ``taps``/``n_pre``.
        has_getwave: expose the GetWave (time-domain) flow to the host; when
            False the host uses the Init (LTI impulse-transform) flow.
    """

    def __init__(self, so_file: str, *, taps=None, n_pre: int = 0,
                 params: str | None = None, has_getwave: bool = False):
        self._lib = ctypes.CDLL(str(so_file))
        c = ctypes
        self._init_fn = self._lib.AMI_Init
        self._init_fn.restype = c.c_long
        self._init_fn.argtypes = [
            c.POINTER(c.c_double), c.c_long, c.c_long, c.c_double, c.c_double,
            c.c_char_p, c.POINTER(c.c_char_p), c.POINTER(c.c_void_p),
            c.POINTER(c.c_char_p)]
        self._gw_fn = self._lib.AMI_GetWave
        self._gw_fn.restype = c.c_long
        self._gw_fn.argtypes = [
            c.POINTER(c.c_double), c.c_long, c.POINTER(c.c_double),
            c.POINTER(c.c_char_p), c.c_void_p]
        self._close_fn = self._lib.AMI_Close
        self._close_fn.restype = c.c_long
        self._close_fn.argtypes = [c.c_void_p]
        self.has_getwave = bool(has_getwave)
        self._handle: ctypes.c_void_p | None = None
        self._params = self._render_params(taps, n_pre, params)

    @staticmethod
    def _render_params(taps, n_pre, params) -> bytes:
        if params is not None:
            return params.encode()
        if taps is not None:
            tap_str = " ".join(f"{float(t):g}" for t in taps)
            return f"(halo_fir (taps {tap_str}) (n_pre {int(n_pre)}))".encode()
        return b"(halo_fir)"

    def _call_init(self, h: np.ndarray, dt: float, ui: float) -> None:
        c = ctypes
        pout = c.c_char_p()
        handle = c.c_void_p()
        msg = c.c_char_p()
        rc = self._init_fn(
            h.ctypes.data_as(c.POINTER(c.c_double)), c.c_long(h.size),
            c.c_long(0), c.c_double(dt), c.c_double(ui), self._params,
            c.byref(pout), c.byref(handle), c.byref(msg))
        if rc != 1:
            raise RuntimeError(f"AMI_Init returned {rc}")
        self._handle = handle
        self.messages = msg.value.decode() if msg.value else ""

    def _ensure_handle(self, dt: float, ui: float) -> None:
        if self._handle is None:              # GetWave path never called init
            self._call_init(np.array([1.0], dtype=np.float64), dt, ui)

    def init(self, impulse: np.ndarray, dt: float, ui: float,
             **params: object) -> np.ndarray:
        h = np.ascontiguousarray(impulse, dtype=np.float64).copy()
        if self._handle is not None:
            self._close_fn(self._handle)
            self._handle = None
        self._call_init(h, dt, ui)            # transforms h in place
        return h

    def get_wave(self, wave: np.ndarray, dt: float,
                 ui: float) -> tuple[np.ndarray, np.ndarray | None]:
        c = ctypes
        self._ensure_handle(dt, ui)
        y = np.ascontiguousarray(wave, dtype=np.float64).copy()
        osr = max(int(round(ui / dt)), 1)
        clk = np.full(y.size // osr + 16, -1.0, dtype=np.float64)
        pout = c.c_char_p()
        rc = self._gw_fn(
            y.ctypes.data_as(c.POINTER(c.c_double)), c.c_long(y.size),
            clk.ctypes.data_as(c.POINTER(c.c_double)), c.byref(pout),
            self._handle)
        if rc != 1:
            raise RuntimeError(f"AMI_GetWave returned {rc}")
        return y, None

    def close(self) -> None:
        if self._handle is not None:
            self._close_fn(self._handle)
            self._handle = None


def load_ami_model(ami_file: str | None = None, dll_file: str | None = None,
                   *, so_file: str | None = None, taps=None, n_pre: int = 0,
                   sample_spaced: bool = True, params=None,
                   has_getwave: bool = False) -> AmiModel:
    """Factory for an :class:`AmiModel`.

    * ``so_file=...`` → :class:`AmiCModel` (a compiled model run over the C ABI);
    * ``ami_file=..., dll_file=...`` → :class:`IbisAmiModel` (pyibisami backend);
    * ``taps=[...], n_pre=k`` → :class:`NativeFirAmi` (dependency-free reference).
    """
    if so_file:
        return AmiCModel(so_file, taps=taps, n_pre=n_pre,
                         params=params if isinstance(params, str) else None,
                         has_getwave=has_getwave)
    if ami_file and dll_file:
        return IbisAmiModel(ami_file, dll_file,
                            params=params if isinstance(params, dict) else None)
    if taps is None:
        taps = [1.0]
    return NativeFirAmi(taps, n_pre=n_pre, sample_spaced=sample_spaced)


# --------------------------------------------------------------------------- #
# COM seam
# --------------------------------------------------------------------------- #


@dataclass
class ComResult:
    """Channel Operating Margin summary."""

    com_db: float                 # 20 log10(A_signal / A_noise) [dB]
    a_signal: float               # available signal amplitude [V]
    a_noise: float                # aggregate noise amplitude at target DER [V]
    fom_isi: float                # residual-ISI contribution [V rms]
    fom_xtalk: float              # crosstalk contribution [V rms]
    fom_noise: float              # thermal/quantization contribution [V rms]
    fom_jitter: float             # jitter-induced contribution [V rms]
    detail: dict = field(default_factory=dict)

    def summary(self) -> str:
        return (f"COM={self.com_db:.2f} dB  (A_s={self.a_signal:.4f} V, "
                f"A_n={self.a_noise:.4f} V; ISI={self.fom_isi:.4f} "
                f"XT={self.fom_xtalk:.4f} N={self.fom_noise:.4f} "
                f"J={self.fom_jitter:.4f})")


class ComAdapter(ABC):
    """Seam for a Channel Operating Margin engine (IEEE 802.3 93A/178A)."""

    @abstractmethod
    def compute(self, channel, cfg, *, xtalk_pulses=None) -> ComResult:
        """Return the COM of ``channel`` under link config ``cfg`` [dB]."""


class NativeCom(ComAdapter):
    """Simplified behavioral COM from the equalized pulse response.

    This is **not** the official 802.3 COM tool — it is a transparent,
    dependency-free figure of merit built from the same ingredients:

        COM = 20 log10( A_s / (Q_target * sigma_total) )

    where ``A_s`` is the main-cursor amplitude after a reference Tx-FIR +
    Rx-FFE + N_b-tap DFE, and ``sigma_total`` combines residual ISI, crosstalk,
    device noise, and jitter (BBN) as an RSS. The official tool plugs in behind
    the identical :meth:`compute` signature; results here are labeled
    "behavioral COM" wherever reported.

    Args:
        target_der: target detector error ratio setting ``Q_target``
            (default 1e-4, 802.3 electrical baseline -> Q ~= 3.719).
        n_dfe: reference DFE tap count (postcursors treated as cancelled).
        rx_ffe_taps / rx_ffe_pre: reference Rx FFE for the signal path.
    """

    def __init__(self, target_der: float = 1e-4, n_dfe: int = 1,
                 rx_ffe_taps: int = 15, rx_ffe_pre: int = 4):
        self.target_der = float(target_der)
        self.n_dfe = int(n_dfe)
        self.rx_ffe_taps = int(rx_ffe_taps)
        self.rx_ffe_pre = int(rx_ffe_pre)

    def _q_target(self) -> float:
        from ..analysis.metrics import qfunc_inv

        return float(qfunc_inv(self.target_der))

    def compute(self, channel, cfg, *, xtalk_pulses=None) -> ComResult:
        from ..channel.response import pulse_from_impulse
        from ..core.waveform import Waveform
        from ..dsp.ffe import channel_cursors, equalized_cursors, mmse_ffe

        osr = cfg.osr
        # channel + reference Rx CTLE folded into one impulse (V/sample)
        rs = channel.response_set(cfg.dt)
        h = rs.h.y
        if cfg.rx.ctle.enable:
            from ..afe import Ctle

            ctle = Ctle.from_config(cfg.rx.ctle, cfg.f_nyquist)
            nfft = int(2 ** np.ceil(np.log2(h.size * 4)))
            f = np.fft.rfftfreq(nfft, d=cfg.dt)
            h = np.fft.irfft(np.fft.rfft(h, nfft) * ctle.transfer(f),
                             nfft)[: h.size * 2]
        # Tx FIR into the impulse (LTI)
        if len(cfg.tx.fir_taps) > 1:
            tx = NativeFirAmi(cfg.tx.fir_taps, cfg.tx.fir_n_pre,
                              sample_spaced=False)
            h = tx.init(h, cfg.dt, cfg.ui)

        pulse = pulse_from_impulse(Waveform(h, cfg.dt), osr)
        peak = int(np.argmax(np.abs(pulse.y)))
        n_pre_c = self.rx_ffe_pre + 4
        n_post_c = self.rx_ffe_taps + 12
        cursors = channel_cursors(pulse, osr, n_pre_c, n_post_c, peak_idx=peak)
        w = mmse_ffe(cursors, n_pre_c, self.rx_ffe_taps, self.rx_ffe_pre,
                     noise_var=max(cfg.rx.noise_rms, 1e-6) ** 2)
        eq, eq_pre = equalized_cursors(cursors, w, n_pre_c, self.rx_ffe_pre)

        main = float(abs(eq[eq_pre])) * cfg.tx.swing
        # PAM4 uses the RLM-reduced outer level spacing as the signal amplitude
        if cfg.modulation == "pam4":
            main *= cfg.tx.rlm / 3.0

        # residual ISI: precursors (all) + postcursors the reference DFE does
        # NOT cancel. In `eq` the main sits at eq_pre; postcursors follow it.
        n_post_cancel = max(0, min(self.n_dfe, eq.size - eq_pre - 1))
        pre_cursors = eq[:eq_pre]
        post_cursors = eq[eq_pre + 1 + n_post_cancel:]
        resid = np.abs(np.concatenate([pre_cursors, post_cursors]))
        # symbol amplitude variance factor: NRZ +-1, PAM4 levels {+-1,+-1/3}
        if cfg.modulation == "pam4":
            sym_var = np.mean(np.array([1.0, 1 / 3, -1 / 3, -1.0]) ** 2)
        else:
            sym_var = 1.0
        sigma_isi = float(np.sqrt(np.sum(resid ** 2) * sym_var)) * cfg.tx.swing

        # crosstalk: RSS of aggressor pulse peaks (independent, worst-align RSS)
        sigma_xt = 0.0
        if xtalk_pulses:
            peaks = [float(np.max(np.abs(np.asarray(getattr(p, "y", p)))))
                     for p in xtalk_pulses]
            sigma_xt = float(np.sqrt(np.sum(np.square(peaks))))

        sigma_n = float(cfg.rx.noise_rms)

        # jitter: Tx RJ (+ DCD as bounded) converted to voltage via pulse slope
        slope = float(np.max(np.abs(np.diff(pulse.y)))) / cfg.dt  # V/s at edge
        rj_s = cfg.tx.rj_ui * cfg.ui
        sigma_j = slope * rj_s * cfg.tx.swing / max(abs(pulse.y[peak]), 1e-12)

        q = self._q_target()
        sigma_tot = float(np.sqrt(sigma_isi ** 2 + sigma_xt ** 2
                                  + sigma_n ** 2 + sigma_j ** 2))
        a_noise = q * sigma_tot
        com = 20.0 * np.log10(max(main, 1e-18) / max(a_noise, 1e-18))
        return ComResult(
            com_db=float(com), a_signal=main, a_noise=a_noise,
            fom_isi=sigma_isi, fom_xtalk=sigma_xt, fom_noise=sigma_n,
            fom_jitter=float(sigma_j),
            detail={"q_target": q, "main_cursor": float(abs(eq[eq_pre])),
                    "ffe_taps": w, "target_der": self.target_der})


class Com93a(ComAdapter):
    """Faithful IEEE 802.3 COM (Clause 93A/178A) behind the same seam.

    Unlike :class:`NativeCom` (a transparent RSS figure of merit), this
    optimizes the equalizer over a grid by FOM, derives the DFE taps from the
    cursors with a ``b_max`` bound, and reads the noise amplitude ``A_ni`` off
    the *convolved* interference-plus-noise PDF at the target DER — the actual
    802.3 method. The heavy lifting lives in
    :func:`halo_serdes.analysis.com.compute_com`; this is a thin seam adapter
    that returns the shared :class:`ComResult` shape (extra fields in
    ``detail``).
    """

    def __init__(self, params=None):
        self.params = params

    def compute(self, channel, cfg, *, xtalk_pulses=None) -> ComResult:
        from ..analysis.com import compute_com

        r = compute_com(channel, cfg, xtalk_pulses=xtalk_pulses,
                        params=self.params)
        detail = dict(r.detail)
        detail["fom_db"] = r.fom_db
        detail["method"] = "802.3-93A/178A"
        return ComResult(
            com_db=r.com_db, a_signal=r.a_signal, a_noise=r.a_noise,
            fom_isi=r.fom_isi, fom_xtalk=r.fom_xtalk, fom_noise=r.fom_noise,
            fom_jitter=r.fom_jitter, detail=detail)
