"""Light analytic checks; these are test fixtures, not native demo evidence."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
from eyes import causal_fit, channel, difference, source, waveforms


CONDITION = {'bit_rate_hz':1.6e9,'driver_low_v':0,'driver_high_v':1.5,
             'rise_fall_s':2e-10,'bits':32,'victim_seed':871,'aggressor_seed':40,
             'time_step_s':2.5e-12,'aggressor_phase_ui':.5}
F = np.r_[1e7,np.arange(1,41)*2.5e8]


class ChannelTests(unittest.TestCase):
    def test_real_causal_fit_preserves_exact_matched_dc(self):
        target=np.column_stack([.5*np.exp(-2j*np.pi*F*25e-12),np.zeros(len(F))])
        taps,report=causal_fit(F,target,CONDITION['time_step_s'])
        np.testing.assert_allclose(taps.sum(axis=0),[.5,0],atol=1e-12)
        self.assertLess(report['maximum_native_loaded_complex_fit_error'],.002)
        self.assertLess(np.max(abs(taps[:,1])),1e-12)

    def test_zero_mutual_control_has_identical_eyes_and_half_source_voltage(self):
        s=np.zeros((len(F),4,4),complex)
        s[:,3,2]=np.exp(-2j*np.pi*F*25e-12)
        w=waveforms(F,s,CONDITION)
        np.testing.assert_allclose(w['quiet'],w['switching'],atol=1e-12)
        self.assertLess(np.max(abs(w['noise'])),1e-12)
        self.assertLess(abs(w['quiet'].max()-.75),.005)
        self.assertLess(w['fit']['pre_input_voltage_residual_v'],1e-10)

    def test_coupled_causal_modes_produce_noise_without_changing_victim_bits(self):
        s=np.zeros((len(F),4,4),complex)
        e1=np.exp(-2j*np.pi*F*20e-12)
        e2=np.exp(-2j*np.pi*F*40e-12)
        s[:,3,2]=(e1+e2)/2
        s[:,3,1]=(e1-e2)/2
        w=waveforms(F,s,CONDITION)
        self.assertGreater(np.max(abs(w['noise'])),.01)
        np.testing.assert_allclose(w['switching']-w['quiet'],w['noise'],atol=1e-12)
        half=waveforms(F,s,CONDITION,dt=1.25e-12)
        self.assertLess(difference(w,half),.005)

    def test_future_bits_do_not_change_earlier_source(self):
        t=np.arange(0,8e-9,CONDITION['time_step_s'])
        a=np.zeros(8); b=a.copy(); b[7]=1
        va=source(t,a,CONDITION,0);vb=source(t,b,CONDITION,0)
        before=t<2e-9+7/CONDITION['bit_rate_hz']
        np.testing.assert_array_equal(va[before],vb[before])

    def test_missing_native_output_never_generates_a_channel(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileNotFoundError):channel(Path(d))


if __name__=='__main__':unittest.main()
