import sys
import unittest
from decimal import Decimal
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from services.stock_ats_cutover import Baseline, Reservation, Event, CutoverError, project_available

class CutoverTests(unittest.TestCase):
    def setUp(self):
        # Synthetic item identity; cutoff aligned to Owner decision for projector math.
        self.b=Baseline('dummy','YGY-MAIN','DUMMY','PCS',Decimal(20000),
                        '2026-11-02T23:22:00+07:00','a'*64)
    def event(self,kind,qty,seq=1,key='',time='2026-11-03T00:00:00+07:00',reverse=''):
        return Event('e'+str(seq),'YGY-MAIN','DUMMY','PCS',time,seq,kind,Decimal(qty),key,reverse)
    def run_projection(self,events=(),reservations=()):
        return project_available(self.b,reservations_at_cutoff=reservations,events=events,
             evidence_complete=True,as_of='2026-11-03T01:00:00+07:00')
    def test_baseline(self):
        self.assertEqual(self.run_projection()['available_qty'],'20000')
    def test_history_not_reapplied(self):
        e=self.event('ISSUE_FREE',1000,time=self.b.cutoff_at)
        self.assertEqual(self.run_projection([e])['available_qty'],'20000')
    def test_old_po_shipping_does_not_reduce_ats(self):
        p=self.run_projection([self.event('SHIP_RESERVED',1000,key='old-po-line')],
                              [Reservation('old-po-line',Decimal(3000))])
        self.assertEqual(p['available_qty'],'20000')
        self.assertEqual(p['remaining_reservations']['old-po-line'],'2000')
    def test_new_reservation_and_shipping_deduct_once(self):
        events=[self.event('RESERVE',2000,key='new'),self.event('SHIP_RESERVED',1000,2,'new')]
        self.assertEqual(self.run_projection(events)['available_qty'],'18000')
    def test_partial_cancel_returns_only_remaining(self):
        events=[self.event('RESERVE',2000,key='new'),self.event('SHIP_RESERVED',500,2,'new'),
                self.event('RELEASE',1500,3,'new')]
        self.assertEqual(self.run_projection(events)['available_qty'],'19500')
    def test_old_cancel_is_bounded_by_open_reservation(self):
        with self.assertRaises(CutoverError):
            self.run_projection([self.event('RELEASE',3001,key='old')],[Reservation('old',Decimal(3000))])
    def test_receipt_and_unreserved_issue(self):
        self.assertEqual(self.run_projection([self.event('RECEIVE_FREE',1000),
             self.event('ISSUE_FREE',200,2)])['available_qty'],'20800')
    def test_retry_is_idempotent(self):
        e=self.event('RESERVE',1000,key='new')
        self.assertEqual(self.run_projection([e,e])['available_qty'],'19000')
    def test_unknown_reservation_blocks_shipping(self):
        with self.assertRaises(CutoverError):self.run_projection([self.event('SHIP_RESERVED',1000,key='unknown')])
    def test_no_claim_when_event_coverage_missing(self):
        with self.assertRaises(CutoverError):
            project_available(self.b,reservations_at_cutoff=[],events=[],evidence_complete=False,
                              as_of='2026-11-03T00:00:00+07:00')
    def test_purchase_reversal(self):
        self.assertEqual(self.run_projection([self.event('RECEIVE_FREE',1000),
             self.event('REVERSE',0,2,reverse='e1')])['available_qty'],'20000')
    def test_shipment_reversal_restores_reservation_without_inflating_ats(self):
        events=[self.event('RESERVE',1000,key='new'),self.event('SHIP_RESERVED',1000,2,'new'),
                self.event('REVERSE',0,3,reverse='e2')]
        p=self.run_projection(events)
        self.assertEqual(p['available_qty'],'19000');self.assertEqual(p['remaining_reservations']['new'],'1000')
    def test_foreign_warehouse_blocked(self):
        e=self.event('RECEIVE_FREE',1000)
        from dataclasses import replace
        with self.assertRaises(CutoverError):self.run_projection([replace(e,warehouse_id='SMG')])
    def test_unknown_production_event_not_double_deducted(self):
        with self.assertRaises(CutoverError):self.run_projection([self.event('PRODUCTION_CONSUMPTION',1000)])

if __name__=='__main__':unittest.main()
