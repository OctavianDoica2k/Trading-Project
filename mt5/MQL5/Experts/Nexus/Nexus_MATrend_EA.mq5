//+------------------------------------------------------------------+
//|                                             Nexus_MATrend_EA.mq5 |
//|  EA simplu: medie mobilă + „Linii de trend” (Nexus_TrendLines).  |
//+------------------------------------------------------------------+
// =====================================================================================
//  CE FACE ACEST FIȘIER (pe scurt)
// -------------------------------------------------------------------------------------
//  - Lucrează o dată pe lumânare (la deschiderea ei), pe timeframe-ul chartului.
//  - Direcția vine din media mobilă: lumânarea precedentă închisă peste MA = doar buy,
//    sub MA = doar sell.
//  - Intrarea vine din Linii de trend (cea mai apropiată rezistență R și cel mai apropiat
//    suport S, valabile înainte de închiderea lumânării precedente):
//      buy  = lumânarea precedentă s-a închis peste R și peste MA (spargerea rezistenței);
//      sell = lumânarea precedentă s-a închis sub S și sub MA (spargerea suportului).
//  - SL: la media mobilă. TP: InpRR × riscul.
//  - Opțional: închidere când o lumânare se închide de partea cealaltă a MA.
//  - Mărimea poziției: la SL se pierde InpRiskPct % din balanță.
//  - O singură poziție pe simbol (magic number).
//  - Pe un chart live, MA și liniile de trend se adaugă pe grafic; în Strategy Tester
//    (vizual) apar automat.
// =====================================================================================
#property copyright   "Nexus"
#property version     "1.00"
#property description "Strategie simplă: medie mobilă (direcția) + spargerea liniilor de trend Nexus (intrarea)."
#property tester_indicator "Nexus\\Nexus_TrendLines.ex5"

#include <Trade\Trade.mqh>

input group "Medie mobilă"
input int                InpMAPeriod   = 50;           // Perioada MA
input ENUM_MA_METHOD     InpMAMethod   = MODE_EMA;     // Tipul MA
input ENUM_APPLIED_PRICE InpMAPrice    = PRICE_CLOSE;  // Prețul pe care se calculează MA
input group "Linii de trend"
input int                InpTLBars     = 1000;         // Lumânări închise din urmă analizate
input bool               InpShowLines  = true;         // Desenează liniile pe chart (încetinește testul)
input group "Ieșiri"
input double             InpRR         = 2.0;          // Raport risc/câștig (TP = RR × distanța până la SL)
input bool               InpCloseOnMA  = true;         // Închide poziția când prețul se închide de partea cealaltă a MA
input group "Risc"
input double             InpRiskPct    = 1.0;          // Risc pe tranzacție, % din balanță
input ulong              InpMagic      = 20261026;     // Magic number

CTrade   trade;
int      hMA = INVALID_HANDLE;
int      hTL = INVALID_HANDLE;
datetime gBarTime = 0;

//+------------------------------------------------------------------+
int OnInit()
  {
   hMA = iMA(_Symbol, _Period, InpMAPeriod, 0, InpMAMethod, InpMAPrice);
   hTL = iCustom(_Symbol, _Period, "Nexus\\Nexus_TrendLines",
                 InpTLBars, 100, InpShowLines, false, 5);
   if(hMA == INVALID_HANDLE || hTL == INVALID_HANDLE)
     {
      Print("Nu pot încărca MA / indicatorul Nexus (Indicators\\Nexus\\Nexus_TrendLines): ", GetLastError());
      return INIT_FAILED;
     }
   if(!MQLInfoInteger(MQL_TESTER))
     {
      ChartIndicatorAdd(0, 0, hMA);
      if(InpShowLines)
         ChartIndicatorAdd(0, 0, hTL);
     }
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.SetDeviationInPoints(30);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   if(hMA != INVALID_HANDLE) IndicatorRelease(hMA);
   if(hTL != INVALID_HANDLE) IndicatorRelease(hTL);
  }

//+------------------------------------------------------------------+
// Valoarea bufferului la lumânarea precedentă (shift 1).
bool ReadPrev(const int handle, const int buffer, double &v)
  {
   double b[1];
   if(CopyBuffer(handle, buffer, 1, 1, b) != 1)
      return false;
   v = b[0];
   return true;
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

// Lotul la care SL-ul costă InpRiskPct % din balanță, limitat de marja liberă.
double LotsFor(const ENUM_ORDER_TYPE type, const double entry, const double sl)
  {
   double tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
   double ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tv <= 0) tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double perLot = MathAbs(entry - sl) / ts * tv;
   if(perLot <= 0)
      return 0;
   double lots = NormalizeLots(AccountInfoDouble(ACCOUNT_BALANCE) * InpRiskPct / 100.0 / perLot);
   double margin = 0;
   double free = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   while(lots > 0 && OrderCalcMargin(type, _Symbol, lots, entry, margin) && margin > free * 0.95)
     {
      if(lots <= SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN)) return 0;
      lots = NormalizeLots(lots * 0.8);
     }
   return lots;
  }

// Deschide poziția cu SL la MA și TP la InpRR × risc.
void Open(const int dir, const double sl, const string why)
  {
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return;
   double entry = dir > 0 ? tick.ask : tick.bid;
   double risk = dir > 0 ? entry - sl : sl - entry;
   double minDist = (SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) + 1) * _Point;
   if(risk < minDist)
      return;                         // MA e prea aproape (sau de partea greșită) a prețului
   double tp = dir > 0 ? entry + InpRR * risk : entry - InpRR * risk;
   ENUM_ORDER_TYPE type = dir > 0 ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double lots = LotsFor(type, entry, sl);
   if(lots <= 0)
      return;
   double nsl = NormalizeDouble(sl, _Digits), ntp = NormalizeDouble(tp, _Digits);
   bool ok = dir > 0 ? trade.Buy(lots, _Symbol, 0, nsl, ntp, "Nexus " + why)
                     : trade.Sell(lots, _Symbol, 0, nsl, ntp, "Nexus " + why);
   if(!ok)
      Print("Ordin respins (", why, "): ", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription());
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   datetime t0 = iTime(_Symbol, _Period, 0);
   if(t0 == 0 || t0 == gBarTime)
      return;
   double ma, res, sup;
   if(!ReadPrev(hMA, 0, ma) || !ReadPrev(hTL, 0, res) || !ReadPrev(hTL, 1, sup))
      return;                         // indicatorii nu sunt încă gata: reîncercăm la tick-ul următor
   gBarTime = t0;
   double c1 = iClose(_Symbol, _Period, 1);

   ulong ticket;
   int pos = OurPosition(ticket);
   if(pos != 0 && InpCloseOnMA && (pos > 0 ? c1 < ma : c1 > ma))
     {
      if(trade.PositionClose(ticket))
         pos = 0;
     }
   if(pos != 0)
      return;

   if(c1 > ma && res != EMPTY_VALUE && c1 > res)
      Open(1, ma, "MA + rezistență spartă");
   else if(c1 < ma && sup != EMPTY_VALUE && c1 < sup)
      Open(-1, ma, "MA + suport spart");
  }
//+------------------------------------------------------------------+
