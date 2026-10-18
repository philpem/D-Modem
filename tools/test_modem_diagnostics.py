#!/usr/bin/env python3
"""Validate hexadecimal diagnostic parsing and rejection of empty/invalid evidence."""
from modem_diagnostics import collect, decode_ud


def main():
    # Captured wire-format values, with independently known decimal rates.
    sample = ('DIAG <2A4D3263 20=0C 22=0D65 24=07A7 26=79E0 27=8340>\r\n'
              'DIAG <2A4D3263 54=0000 60=51 80=vendor>\r\nOK\r\n')
    report = decode_ud(sample)
    fields = report['decoded']
    assert report['frames_parsed']==2 and report['keys_parsed']==8
    assert fields['initial_tx_bps']==31200 and fields['initial_rx_bps']==33600
    assert fields['tx_symbols_per_second']==3429 and fields['tx_carrier_hz']==1959
    assert fields['tx_overrun_characters']==0
    assert fields['termination']=='DTE hangup command'
    assert report['raw']['80']=='vendor'
    invalid = decode_ud('DIAG <2A4D3263 10=D7 12=FF>')
    assert invalid['invalid_signal_metrics']==['rx_power_minus_dbm','estimated_noise_minus_dbm']
    for negative in ('', 'OK', 'DIAG <bad 20=0C>', 'Oli\'Net V92 Ready\r\nOK'):
        assert decode_ud(negative)['keys_parsed']==0
    replies={'ATS86?':'020\r\nOK', 'AT&V1':'LAST TX rate................ N/A\r\nOK',
             'AT#UD':sample, 'ATS91?':'ERROR', 'AT%L':'ERROR', 'AT%Q':'ERROR', 'AT&V':'OK'}
    result=collect('other',lambda text:replies[text])
    assert result['queries_attempted']==7 and result['reports_with_data']==3
    assert result['s86']['reason']=='keyboard abort'
    assert not result['unimodem']['connection_measurements_valid']
    assert result['queries']['AT%L']['status']=='unsupported'
    software=collect('software',lambda text:'OK' if text=='AT&V' else 'ERROR')
    assert software['queries_attempted']==3 and software['reports_with_data']==0
    implausible=collect('usr',lambda text:'SNR ( dB ) 6482.1\r\nOK' if text=='ATI11' else 'ERROR')
    assert implausible['invalid_reported_metrics']==['SNR']
    missing=collect('other',lambda text:None)
    assert missing['queries_attempted']==0 and missing['reports_with_data']==0
    assert all(q['status']=='not_queried' for q in missing['queries'].values())
    print('PASS: #UD rates/counters/unknown keys; 4 negative frame controls; invalid power and untrained-call evidence; 0/3 software reports')


if __name__=='__main__':
    main()
