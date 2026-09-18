import { HeroSection } from "@/components/hero-section";
import { AgentsSection } from "@/components/agents-section";
import { AboutSection } from "@/components/about-section";
import { DashboardSection } from "@/components/dashboard-section";
import { HarnessSection } from "@/components/harness-section";
import { McpSection } from "@/components/mcp-section";
import { DemoVideoSection } from "@/components/demo-video-section";
import { FooterSection } from "@/components/footer-section";

function App() {
  return (
    <main className="min-h-screen bg-background">
      <HeroSection />
      <AgentsSection />
      <AboutSection />
      <DashboardSection />
      <HarnessSection />
      <McpSection />
      <DemoVideoSection />
      <FooterSection />
    </main>
  );
}

export default App;
