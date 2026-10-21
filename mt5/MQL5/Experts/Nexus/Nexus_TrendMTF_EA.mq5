//+------------------------------------------------------------------+
//|                                            Nexus_TrendMTF_EA.mq5 |
//|  EA care tranzacționează doar cu indicatorii Nexus:              |
//|  „Linii de trend” (Nexus_TrendLines) + „Trend MTF” (Nexus_TrendMTF)|
//+------------------------------------------------------------------+
// =====================================================================================
//  CE FACE ACEST FIȘIER (pe scurt)
// -------------------------------------------------------------------------------------
//  - Lucrează o dată pe lumânare (la deschiderea ei), pe timeframe-ul chartului.
//  - Direcția vine din Trend MTF: perioadele alese (1D / 1W / 1M / 1Y) care sunt în creștere
//    (≥ prag) sau în scădere (≤ −prag). Cumpără doar când cel puțin InpMinAligned perioade
//    sunt în creștere (și mai multe decât cele în scădere); vinde invers.
//  - Intrarea vine din Linii de trend (cea mai apropiată rezistență R și cel mai apropiat
//    suport S, valabile înainte de închiderea lumânării precedente):
//      Breakout: lumânarea precedentă s-a închis peste R (buy) / sub S (sell);
//      Bounce:   lumânarea precedentă s-a închis aproape de S (buy) / de R (sell), în zona
//                InpZonePct din canalul S–R, fără să spargă linia.
//  - SL: dincolo de linie (suportul / linia spartă), cu o marjă de InpSLBufferPct din canal.
//    TP: InpRR × riscul, sau la linia opusă (rezistența pentru buy, suportul pentru sell).
//  - O singură poziție pe simbol (magic number). Opțional: închidere când trendul MTF nu mai
//    e în direcția poziției.
// =====================================================================================
#property copyright   "Nexus"
#property version     "1.00"
#property description "Tranzacționează doar cu Linii de trend + Trend MTF (indicatorii Nexus)."
#property tester_indicator "Nexus\\Nexus_TrendLines.ex5"
#property tester_indicator "Nexus\\Nexus_TrendMTF.ex5"

#include <Trade\Trade.mqh>

enum ENUM_NX_MODE
  {
   NX_BREAKOUT = 0,   // Breakout (spargerea liniei)
   NX_BOUNCE   = 1,   // Bounce (respingere din linie)
   NX_BOTH     = 2    // Ambele
  };

enum ENUM_NX_TP
  {
   NX_TP_RR   = 0,    // Raport risc/câștig (InpRR)
   NX_TP_LINE = 1     // La linia opusă
  };

enum ENUM_NX_BRK_SL
  {
   NX_SL_BROKEN = 0,  // Dincolo de linia spartă
   NX_SL_OPPOSITE = 1 // Dincolo de linia opusă (suport pentru buy)
  };

input group "Linii de trend"
input int            InpTLBars         = 1000;        // Lumânări închise din urmă analizate
input bool           InpShowLines      = false;       // Desenează liniile pe chart (încetinește testul)
input group "Trend MTF"
input bool           InpUseDay         = true;        // Folosește 1D
input bool           InpUseWeek        = true;        // Folosește 1W
input bool           InpUseMonth       = true;        // Folosește 1M
input bool           InpUseYear        = true;        // Folosește 1Y
input double         InpPrag           = 0;           // Pragul minim (%) al fiecărei perioade
input int            InpMinAligned     = 3;           // Câte perioade alese trebuie să fie în direcția tranzacției
input bool           InpWeekMonthRange = true;        // 1W cu maximul − minimul lunii (ca analysis.py)
input group "Intrări"
input ENUM_NX_MODE   InpMode           = NX_BOTH;     // Tipul intrării
input double         InpZonePct        = 20;          // Bounce: distanța max. de linie (% din canalul S–R)
input bool           InpBounceConfirm  = true;        // Bounce: lumânarea precedentă în direcția tranzacției
input double         InpMinChannelPts  = 0;           // Canal S–R minim (puncte, 0 = oricât)
input group "Ieșiri"
input ENUM_NX_BRK_SL InpBreakoutSL     = NX_SL_BROKEN; // Breakout: unde se pune SL
input double         InpSLBufferPct    = 10;          // Marja SL dincolo de linie (% din canalul S–R)
input ENUM_NX_TP     InpTPMode         = NX_TP_RR;    // Take profit
input double         InpRR             = 2.0;         // Raport risc/câștig
input bool           InpCloseOnFlip    = true;        // Închide poziția când trendul MTF nu mai e în direcția ei
input group "Risc"
input double         InpLots           = 0.10;        // Lot fix (când riscul e 0)
input double         InpRiskPct        = 0;           // Risc pe tranzacție, % din balanță (0 = lot fix)
input int            InpMaxSpreadPts   = 0;           // Spread maxim la intrare (puncte, 0 = oricât)
input ulong          InpMagic          = 20261019;    // Magic number

CTrade   trade;
int      hTL  = INVALID_HANDLE;
int      hMTF = INVALID_HANDLE;
datetime gBarTime = 0;

//+------------------------------------------------------------------+
int OnInit()
  {
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
// Direcția din Trend MTF: +1 creștere, −1 scădere, 0 nicio direcție.
int MtfDirection(const double &v[])
  {
   bool use[4];
   use[0] = InpUseDay; use[1] = InpUseWeek; use[2] = InpUseMonth; use[3] = InpUseYear;
   int chosen = 0, up = 0, dn = 0;
   for(int i = 0; i < 4; i++)
     {
      if(!use[i]) continue;
      chosen++;
      if(v[i] == EMPTY_VALUE) continue;
      if(v[i] > 0 && v[i] >= InpPrag)  up++;
      if(v[i] < 0 && -v[i] >= InpPrag) dn++;
     }
   if(chosen == 0)
      return 0;
   int need = MathMax(1, MathMin(InpMinAligned, chosen));
   if(up >= need && up > dn) return 1;
   if(dn >= need && dn > up) return -1;
   return 0;
  }

bool ReadMtf(double &v[])
  {
   for(int i = 0; i < 4; i++)
     {
      double b[1];
      if(CopyBuffer(hMTF, i, 0, 1, b) != 1)
         return false;
      v[i] = b[0];
     }
   return true;
  }

// Bufferele liniilor de trend: [0] = lumânarea curentă, [1] = precedenta.
bool ReadTL(const int buffer, double &out[])
  {
   ArraySetAsSeries(out, true);
   return CopyBuffer(hTL, buffer, 0, 2, out) == 2;
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
      double mn = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
      if(lots <= mn) return 0;
      lots = NormalizeLots(lots * 0.8);
     }
   return lots;
  }

// Deschide poziția dacă SL / TP respectă distanța minimă a brokerului.
void Open(const int dir, const double sl, double tp, const string why)
  {
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return;
   if(InpMaxSpreadPts > 0 && (tick.ask - tick.bid) / _Point > InpMaxSpreadPts)
      return;
   double entry = dir > 0 ? tick.ask : tick.bid;
   double risk = dir > 0 ? entry - sl : sl - entry;
   if(risk <= 0)
      return;
   if(tp == 0)
      tp = dir > 0 ? entry + InpRR * risk : entry - InpRR * risk;
   double minDist = (SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) + 1) * _Point;
   if(risk < minDist || (dir > 0 ? tp - entry : entry - tp) < minDist)
      return;
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
   double mtf[4], res[], sup[];
   if(!ReadMtf(mtf) || !ReadTL(0, res) || !ReadTL(1, sup))
      return;                         // indicatorii nu sunt încă gata: reîncercăm la tick-ul următor
   gBarTime = t0;

   int dir = MtfDirection(mtf);
   ulong ticket;
   int pos = OurPosition(ticket);
   if(pos != 0 && InpCloseOnFlip && dir != pos)
     {
      if(trade.PositionClose(ticket))
         pos = 0;
     }
   if(pos != 0 || dir == 0)
      return;

   double r1 = res[1], s1 = sup[1], r0 = res[0], s0 = sup[0];
   if(r1 == EMPTY_VALUE || s1 == EMPTY_VALUE || r1 <= s1)
      return;                         // fără canal suport–rezistență nu tranzacționăm
   double channel = r1 - s1;
   if(InpMinChannelPts > 0 && channel < InpMinChannelPts * _Point)
      return;
   double buf = InpSLBufferPct / 100.0 * channel;
   double c1 = iClose(_Symbol, _Period, 1), o1 = iOpen(_Symbol, _Period, 1);
   double zone = InpZonePct / 100.0 * channel;

   if(dir > 0)
     {
      if(InpMode != NX_BOUNCE && c1 > r1)
        {
         double ref = InpBreakoutSL == NX_SL_BROKEN ? r1 : s0;
         if(ref == EMPTY_VALUE) return;
         double tp = InpTPMode == NX_TP_LINE && r0 != EMPTY_VALUE && r0 > c1 ? r0 : 0;
         Open(1, ref - buf, tp, "breakout buy");
         return;
        }
      if(InpMode != NX_BREAKOUT && c1 >= s1 && c1 - s1 <= zone && (!InpBounceConfirm || c1 > o1) && s0 != EMPTY_VALUE)
        {
         double tp = InpTPMode == NX_TP_LINE && r0 != EMPTY_VALUE ? r0 : 0;
         Open(1, s0 - buf, tp, "bounce buy");
        }
     }
   else
     {
      if(InpMode != NX_BOUNCE && c1 < s1)
        {
         double ref = InpBreakoutSL == NX_SL_BROKEN ? s1 : r0;
         if(ref == EMPTY_VALUE) return;
         double tp = InpTPMode == NX_TP_LINE && s0 != EMPTY_VALUE && s0 < c1 ? s0 : 0;
         Open(-1, ref + buf, tp, "breakout sell");
         return;
        }
      if(InpMode != NX_BREAKOUT && c1 <= r1 && r1 - c1 <= zone && (!InpBounceConfirm || c1 < o1) && r0 != EMPTY_VALUE)
        {
         double tp = InpTPMode == NX_TP_LINE && s0 != EMPTY_VALUE ? s0 : 0;
         Open(-1, r0 + buf, tp, "bounce sell");
        }
     }
  }
//+------------------------------------------------------------------+
