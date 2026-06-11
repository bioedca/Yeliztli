import { Routes, Route } from 'react-router-dom'
import { Toaster } from 'sonner'
import AppLayout from '@/components/layout/AppLayout'
import AuthGuard from '@/components/AuthGuard'
import ErrorBoundary from '@/components/ui/ErrorBoundary'
import RouteAnnouncer from '@/components/layout/RouteAnnouncer'
import Dashboard from '@/pages/Dashboard'
import VariantExplorer from '@/pages/VariantExplorer'
import VariantDetailPage from '@/pages/VariantDetailPage'
import PharmacogenomicsView from '@/pages/PharmacogenomicsView'
import NutrigenomicsView from '@/pages/NutrigenomicsView'
import CancerView from '@/pages/CancerView'
import CardiovascularView from '@/pages/CardiovascularView'
import MetabolicView from '@/pages/MetabolicView'
import FHView from '@/pages/FHView'
import EBMDView from '@/pages/EBMDView'
import APOEView from '@/pages/APOEView'
import CarrierStatusView from '@/pages/CarrierStatusView'
import AncestryView from '@/pages/AncestryView'
import FitnessView from '@/pages/FitnessView'
import SleepView from '@/pages/SleepView'
import MethylationView from '@/pages/MethylationView'
import SkinView from '@/pages/SkinView'
import AllergyView from '@/pages/AllergyView'
import TraitsPersonalityView from '@/pages/TraitsPersonalityView'
import GeneHealthView from '@/pages/GeneHealthView'
import RareVariantsView from '@/pages/RareVariantsView'
import GenomeBrowser from '@/pages/GenomeBrowser'
import QueryBuilderView from '@/pages/QueryBuilderView'
import OverlaysView from '@/pages/OverlaysView'
import ReportBuilder from '@/pages/ReportBuilder'
import FindingsExplorer from '@/pages/FindingsExplorer'
import GeneDetailPage from '@/pages/GeneDetailPage'
import IndividualDetail from '@/pages/IndividualDetail'
import ConcordanceReport from '@/pages/ConcordanceReport'
import Settings from '@/pages/Settings'
import SetupWizard from '@/pages/SetupWizard'
import Login from '@/pages/Login'

export default function App() {
  return (
    <ErrorBoundary>
      <RouteAnnouncer />
      <Routes>
      {/* Full-screen pages (no sidebar/nav, no auth guard) */}
      <Route path="/setup" element={<SetupWizard />} />
      <Route path="/login" element={<Login />} />

      {/* Auth-protected routes */}
      <Route element={<AuthGuard />}>
        {/* Main app layout with sidebar */}
        <Route element={<AppLayout />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/findings" element={<FindingsExplorer />} />
          <Route path="/variants" element={<VariantExplorer />} />
          <Route path="/variants/:rsid" element={<VariantDetailPage />} />
          <Route path="/genes/:symbol" element={<GeneDetailPage />} />
          <Route path="/individuals/:id" element={<IndividualDetail />} />
          <Route path="/samples/:id/concordance" element={<ConcordanceReport />} />
          <Route path="/pharmacogenomics" element={<PharmacogenomicsView />} />
          <Route path="/nutrigenomics" element={<NutrigenomicsView />} />
          <Route path="/cancer" element={<CancerView />} />
          <Route path="/cardiovascular" element={<CardiovascularView />} />
          <Route path="/metabolic" element={<MetabolicView />} />
          <Route path="/fh" element={<FHView />} />
          <Route path="/ebmd" element={<EBMDView />} />
          <Route path="/apoe" element={<APOEView />} />
          <Route path="/carrier-status" element={<CarrierStatusView />} />
          <Route path="/ancestry" element={<AncestryView />} />
          <Route path="/fitness" element={<FitnessView />} />
          <Route path="/sleep" element={<SleepView />} />
          <Route path="/methylation" element={<MethylationView />} />
          <Route path="/skin" element={<SkinView />} />
          <Route path="/allergy" element={<AllergyView />} />
          <Route path="/traits" element={<TraitsPersonalityView />} />
          <Route path="/gene-health" element={<GeneHealthView />} />
          <Route path="/rare-variants" element={<RareVariantsView />} />
          <Route path="/genome-browser" element={<GenomeBrowser />} />
          <Route path="/query-builder" element={<QueryBuilderView />} />
          <Route path="/overlays" element={<OverlaysView />} />
          <Route path="/reports" element={<ReportBuilder />} />
          <Route path="/settings/*" element={<Settings />} />
        </Route>
      </Route>
      </Routes>
      <Toaster position="bottom-right" theme="system" closeButton />
    </ErrorBoundary>
  )
}
