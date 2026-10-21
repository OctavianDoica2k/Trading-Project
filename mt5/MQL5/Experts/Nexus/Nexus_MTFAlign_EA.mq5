//+------------------------------------------------------------------+
//|                                            Nexus_MTFAlign_EA.mq5 |
//|  Intrare: Trend MTF cu 1D · 1W · 1M · 1Y aliniate peste prag.    |
//|  Ieșire:  prima linie de trend din direcția tranzacției, sau SL. |
//+------------------------------------------------------------------+
// =====================================================================================
//  CE FACE ACEST FIȘIER (pe scurt)
// -------------------------------------------------------------------------------------
//  - INTRAREA vine din Trend MTF (Nexus_TrendMTF): toate cele patru perioade (1D, 1W, 1M,
//    1Y) au același semn și fiecare depășește pragul InpPrag (în valoare absolută):
//      toate > +InpPrag  → BUY
//      toate < −InpPrag  → SELL
//    Valorile MTF vin din lumânările D1 ÎNCHISE, deci se schimbă o dată pe zi; semnalul se
//    verifică la deschiderea fiecărei lumânări a chartului. Cel mult InpMaxTradesPerDay
//    intrări pe zi (altfel, după fiecare ieșire, EA-ul ar reintra imediat pe același semnal).
//  - IEȘIREA vine din Linii de trend (Nexus_TrendLines): prima linie din proximitatea
//    prețului, în direcția tranzacției:
//      BUY  → cea mai apropiată rezistență de deasupra prețului (dreaptă prin maxime)
//      SELL → cel mai apropiat suport de sub preț (dreaptă prin minime)
//    Linia e oblică, deci nivelul se schimbă de la o lumânare la alta: la fiecare lumânare
//    nouă TP-ul poziției e mutat pe valoarea liniei, iar la fiecare tick se verifică și
//    atingerea (în caz că brokerul nu acceptă TP-ul atât de aproape).
//  - STOP LOSS fix, în puncte (InpSLPoints) sau în % din preț (InpSLPct) — parametrul de
//    optimizat împreună cu InpPrag.
//  - O singură poziție pe simbol (magic number).
// =====================================================================================
#property copyright   "Nexus"
#property version     "1.00"
#property description "Intrare: Trend MTF cu toate cele 4 perioade aliniate peste prag. Ieșire: prima linie de trend din direcția tranzacției, sau SL."
#property tester_indicator "Nexus\\Nexus_TrendLines.ex5"
#property tester_indicator "Nexus\\Nexus_TrendMTF.ex5"

#include <Trade\Trade.mqh>

enum ENUM_NX_SL
  {
   NX_SL_POINTS = 0,  // În puncte (InpSLPoints)
   NX_SL_PCT    = 1   // În % din prețul de intrare (InpSLPct)
  };

input group "Trend MTF (intrarea)"
input double     InpPrag            = 50;           // Pragul X: fiecare perioadă peste X% (30–90)
input bool       InpWeekMonthRange  = true;         // 1W cu maximul − minimul lunii (ca analysis.py)
input int        InpMaxTradesPerDay = 1;            // Intrări maxime pe zi (0 = oricâte)
input bool       InpCloseOnMtfBreak = false;        // Închide și când alinierea dispare
input group "Linii de trend (ieșirea)"
input int        InpTLBars          = 1000;         // Lumânări închise din urmă analizate
input bool       InpRequireTarget   = true;         // Intră doar dacă există o linie țintă
input double     InpMinTargetPts    = 0;            // Distanța minimă până la linia țintă (puncte)
input bool       InpShowLines       = false;        // Desenează liniile pe chart (încetinește testul)
input group "Stop loss"
input ENUM_NX_SL InpSLMode          = NX_SL_POINTS; // Tipul stop loss-ului
input int        InpSLPoints        = 500;          // SL în puncte
input double     InpSLPct           = 1.0;          // SL în % din preț
input group "Risc"
input double     InpLots            = 0.10;         // Lot fix (când riscul e 0)
input double     InpRiskPct         = 0;            // Risc pe tranzacție, % din balanță (0 = lot fix)
input int        InpMaxSpreadPts    = 0;            // Spread maxim la intrare (puncte, 0 = oricât)
input ulong      InpMagic           = 20261020;     // Magic number

CTrade   trade;
int      hTL  = INVALID_HANDLE;
int      hMTF = INVALID_HANDLE;
datetime gBarTime  = 0;
double   gRes      = EMPTY_VALUE;   // rezistența cea mai apropiată, pe lumânarea curentă
double   gSup      = EMPTY_VALUE;   // suportul cel mai apropiat, pe lumânarea curentă
datetime gDay      = 0;             // ziua pentru care numărăm intrările
int      gDayCount = 0;

//+------------------------------------------------------------------+
int OnInit()
  {
   if(InpSLMode == NX_SL_POINTS && InpSLPoints <= 0) return INIT_PARAMETERS_INCORRECT;
   if(InpSLMode == NX_SL_PCT && InpSLPct <= 0)       return INIT_PARAMETERS_INCORRECT;
   hTL = iCustom(_Symbol, _Period, "Nexus\\Nexus_TrendLines",
                 InpTLBars, 100, InpShowLines, false, 5);
   hMTF = iCustom(_Symbol, _Period, "Nexus\\Nexus_TrendMTF",
                  InpPrag, InpWeekMonthRange, false, 10, 25, 10);
   if(hTL == INVALID_HANDLE || hMTF == INVALID_HANDLE)
     {
      Print("Nu pot încărca indicatorii Nexus (Indicators\\Nexus\\Nexus_TrendLines / Nexus_TrendMTF): ", GetLastError());
      return INIT_FAILED;
     }
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.SetDeviationInPoints(30);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   if(hTL != INVALID_HANDLE)  IndicatorRelease(hTL);
   if(hMTF != INVALID_HANDLE) IndicatorRelease(hMTF);
  }

//+------------------------------------------------------------------+
// +1 = toate patru > +InpPrag, −1 = toate patru < −InpPrag, 0 = nealiniate.
int MtfSignal()
  {
   int up = 0, dn = 0;
   for(int i = 0; i < 4; i++)
     {
      double b[1];
      if(CopyBuffer(hMTF, i, 0, 1, b) != 1 || b[0] == EMPTY_VALUE)
         return 0;
      if(b[0] > InpPrag)  up++;
      if(b[0] < -InpPrag) dn++;
     }
   return up == 4 ? 1 : dn == 4 ? -1 : 0;
  }

// Liniile de trend ale lumânării curente (buffer 0 = rezistența, 1 = suportul).
bool ReadLines()
  {
   double r[1], s[1];
   if(CopyBuffer(hTL, 0, 0, 1, r) != 1 || CopyBuffer(hTL, 1, 0, 1, s) != 1)
      return false;
   gRes = r[0];
   gSup = s[0];
   return true;
  }

// Linia țintă pentru o poziție în direcția dir (0 = nu există).
double Target(const int dir)
  {
   double v = dir > 0 ? gRes : gSup;
   return v == EMPTY_VALUE ? 0 : v;
  }

// Poziția noastră pe simbol: +1 buy, −1 sell, 0 niciuna.
int OurPosition(ulong &ticket)
  {
   ticket = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol || (ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      ticket = t;
      return PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? 1 : -1;
     }
   return 0;
  }

double NormalizeLots(double lots)
  {
   double mn = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double mx = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double st = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(st <= 0) st = mn;
   lots = MathFloor(lots / st + 1e-9) * st;
   lots = MathMax(mn, MathMin(mx, lots));
   return NormalizeDouble(lots, 8);
  }

double LotsFor(const ENUM_ORDER_TYPE type, const double entry, const double sl)
  {
   double lots = InpLots;
   if(InpRiskPct > 0)
     {
      double tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
      double ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
      if(tv <= 0) tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
      double perLot = MathAbs(entry - sl) / ts * tv;
      if(perLot <= 0)
         return 0;
      lots = AccountInfoDouble(ACCOUNT_BALANCE) * InpRiskPct / 100.0 / perLot;
     }
   lots = NormalizeLots(lots);
   // Nu deschidem mai mult decât permite marja liberă.
   double margin = 0;
   double free = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   while(lots > 0 && OrderCalcMargin(type, _Symbol, lots, entry, margin) && margin > free * 0.95)
     {
      if(lots <= SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN)) return 0;
      lots = NormalizeLots(lots * 0.8);
     }
   return lots;
  }

double MinStopDist()
  {
   return (SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) + 1) * _Point;
  }

void TryOpen(const int dir)
  {
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return;
   if(InpMaxSpreadPts > 0 && (tick.ask - tick.bid) / _Point > InpMaxSpreadPts)
      return;
   double entry = dir > 0 ? tick.ask : tick.bid;
   double dist = InpSLMode == NX_SL_POINTS ? InpSLPoints * _Point : entry * InpSLPct / 100.0;
   double sl = dir > 0 ? entry - dist : entry + dist;
   double minDist = MinStopDist();
   if(dist < minDist)
      return;

   // Linia țintă trebuie să fie în fața prețului (deasupra pentru buy, dedesubt pentru sell).
   double tgt = Target(dir), tp = 0;
   if(tgt > 0)
     {
      double room = dir > 0 ? tgt - entry : entry - tgt;
      if(room < MathMax(minDist, InpMinTargetPts * _Point))
         return;                       // prețul e deja pe linie: am ieși imediat
      tp = tgt;
     }
   else if(InpRequireTarget)
      return;

   ENUM_ORDER_TYPE type = dir > 0 ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double lots = LotsFor(type, entry, sl);
   if(lots <= 0)
      return;
   double nsl = NormalizeDouble(sl, _Digits), ntp = NormalizeDouble(tp, _Digits);
   bool ok = dir > 0 ? trade.Buy(lots, _Symbol, 0, nsl, ntp, "Nexus MTF align buy")
                     : trade.Sell(lots, _Symbol, 0, nsl, ntp, "Nexus MTF align sell");
   if(ok && (trade.ResultRetcode() == TRADE_RETCODE_DONE || trade.ResultRetcode() == TRADE_RETCODE_PLACED))
      gDayCount++;
   else
      Print("Ordin respins: ", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription());
  }

// Mută TP-ul poziției pe linia țintă a lumânării curente (linia e oblică).
void UpdateTP(const ulong ticket, const int dir)
  {
   if(!PositionSelectByTicket(ticket))
      return;
   double tgt = Target(dir);
   double tp = tgt > 0 ? NormalizeDouble(tgt, _Digits) : 0;
   double cur = PositionGetDouble(POSITION_TP);
   if(MathAbs(tp - cur) < _Point / 2)
      return;
   double price = dir > 0 ? SymbolInfoDouble(_Symbol, SYMBOL_BID) : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   // Prea aproape pentru broker: rămâne TP-ul vechi, iar atingerea o prinde verificarea pe tick.
   if(tp > 0 && (dir > 0 ? tp - price : price - tp) < MinStopDist())
      return;
   trade.PositionModify(ticket, PositionGetDouble(POSITION_SL), tp);
  }

// Ieșirea la atingerea liniei țintă (în completarea TP-ului).
bool TouchedTarget(const int dir)
  {
   double tgt = Target(dir);
   if(tgt <= 0)
      return false;
   return dir > 0 ? SymbolInfoDouble(_Symbol, SYMBOL_BID) >= tgt
                  : SymbolInfoDouble(_Symbol, SYMBOL_ASK) <= tgt;
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   ulong ticket;
   int pos = OurPosition(ticket);

   datetime t0 = iTime(_Symbol, _Period, 0);
   if(t0 != 0 && t0 != gBarTime)
     {
      if(!ReadLines())
         return;                       // indicatorii nu sunt încă gata: reîncercăm la tick-ul următor
      int sig = MtfSignal();
      gBarTime = t0;

      datetime day = t0 - (t0 % 86400);
      if(day != gDay) { gDay = day; gDayCount = 0; }

      if(pos != 0 && InpCloseOnMtfBreak && sig != pos)
        {
         if(trade.PositionClose(ticket))
            pos = 0;
        }
      if(pos != 0)
         UpdateTP(ticket, pos);
      else if(sig != 0 && (InpMaxTradesPerDay <= 0 || gDayCount < InpMaxTradesPerDay))
        {
         TryOpen(sig);
         return;
        }
     }

   if(pos != 0 && gBarTime != 0 && TouchedTarget(pos))
      trade.PositionClose(ticket);
  }
//+------------------------------------------------------------------+
