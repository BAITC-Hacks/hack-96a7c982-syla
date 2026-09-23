"""50-scenario anomaly matrix for graceful agent behavior."""
import math
import unittest
from types import SimpleNamespace
import pandas as pd
from agent import Agent

CHANNELS={"push":{"cost_per_contact":0,"conversion_multiplier":.5},
"sms":{"cost_per_contact":4,"conversion_multiplier":.65},
"digital_ads":{"cost_per_contact":22,"conversion_multiplier":.85},
"call":{"cost_per_contact":160,"conversion_multiplier":1.2}}

def make_env(case):
    n=case % 17
    profile=pd.DataFrame({
      "ID_NUMBER":range(n),"current_tariff":["tariff_1"]*n,
      "arpu_segment":["MID"]*n,"data_segment":["LITE"]*n,
      "call_segment":["MEDIUM"]*n,
      "predicted_arpu":[0.0 if case%11==0 else (1e9 if case%13==0 else 3000.0)]*n})
    tariffs=pd.DataFrame({"tariff_plan_code":["tariff_1","tariff_2"],
                          "price_tariff":[0.0, 1e9 if case%7==0 else 5000.0]})
    budget=[0,-1,3,100000,float("inf")][case%5]
    contacts=[0,1,9,100,15000][case%5]
    pilots=[0,1,20][case%3]
    env=SimpleNamespace(customer_profile=profile,tariffs=tariffs,channels=dict(CHANNELS),
      remaining_budget=budget,remaining_contacts=contacts,pilots_left=pilots,pilot_history=[])
    def pilot(**kwargs):
        mode=case%10
        if mode==0: raise RuntimeError("timeout/500")
        if mode==1: return {}
        if mode==2: return {"observed_lift_ratio":"bad","n_customers":10}
        if mode==3: return {"observed_lift_ratio":float("nan"),"n_customers":10}
        size=min(int(kwargs.get("n_customers",10)), max(1,n))
        cost=size*env.channels[kwargs["channel"]]["cost_per_contact"]
        if env.remaining_contacts < size or env.remaining_budget < cost:
            raise RuntimeError("resource exhausted")
        env.remaining_contacts-=size; env.remaining_budget-=cost; env.pilots_left-=1
        return {"observed_lift_ratio":(-.5 if mode==4 else .2),"n_customers":size}
    env.run_pilot=pilot
    return env

class EdgeCaseMatrix(unittest.TestCase):
    def test_50_anomalous_environments_never_escape(self):
        for case in range(50):
            with self.subTest(case=case):
                try:
                    result=Agent().act(make_env(case))
                except Exception as exc:
                    self.fail(f"case {case} uncaught: {type(exc).__name__}: {exc}")
                self.assertIsInstance(result,list)
                self.assertLessEqual(len(result),10)
                for c in result:
                    self.assertIn(c.get("target_tariff"),{"tariff_1","tariff_2"})
                    self.assertIn(c.get("channel"),CHANNELS)

if __name__=="__main__":
    unittest.main()
